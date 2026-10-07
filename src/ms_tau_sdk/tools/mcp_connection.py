"""Adapt the tools and resources of one MCP connection to Tau."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from mcp import types
from tau_agent.messages import ImageContent, TextContent
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue

_INVALID_TOOL_NAME = re.compile(r"[^A-Za-z0-9_-]")


class MCPConnection(Protocol):
    """The part of an MCP connection client that the adapter uses."""

    tools: tuple[types.Tool, ...]
    resources: tuple[types.Resource, ...]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        meta: dict[str, object] | None = None,
    ) -> types.CallToolResult: ...

    async def read_resource(self, uri: str) -> types.ReadResourceResult: ...


@dataclass(frozen=True)
class MCPToolPolicy:
    """How one connection's tools are offered to the model.

    ``prefix`` names the connection's tools (``<prefix>__<tool>``) and ``display_name`` appears in
    the messages the model sees. The hooks let a connection exclude tools, reshape an input schema,
    check arguments before a call, attach private metadata the model never sees, and refuse a call
    before it is sent.
    """

    prefix: str
    display_name: str
    excluded_tools: frozenset[str] = frozenset()
    input_schema: Callable[[types.Tool], Mapping[str, JSONValue]] | None = None
    check_arguments: Callable[[str, Mapping[str, JSONValue]], None] | None = None
    private_meta: Callable[[types.Tool], Mapping[str, JSONValue] | None] | None = None
    refusal: Callable[[types.Tool], str | None] | None = None
    resource_tool_description: str | None = None


def mcp_tool_name(prefix: str, mcp_name: str) -> str:
    return f"{prefix}__{_INVALID_TOOL_NAME.sub('_', mcp_name)}"


def mcp_resource_tool_name(prefix: str) -> str:
    return f"{prefix}__read_resource"


def _tool_label(tool: types.Tool) -> str:
    if tool.annotations is not None and tool.annotations.title:
        return tool.annotations.title
    return tool.title or tool.name


def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def mcp_tool_result(
    *,
    canonical_name: str,
    result: types.CallToolResult,
    display_name: str,
) -> AgentToolResult:
    content: list[TextContent | ImageContent] = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            content.append(TextContent(text=block.text))
        elif isinstance(block, types.ImageContent):
            content.append(
                ImageContent(
                    data=block.data,
                    mime_type=block.mimeType,
                )
            )
        else:
            content.append(TextContent(text=_json_text(block.model_dump(mode="json"))))
    if not content and result.structuredContent is not None:
        content.append(TextContent(text=_json_text(result.structuredContent)))
    if not content and result.isError:
        content.append(TextContent(text=f"{display_name} MCP tool {canonical_name} failed."))

    details: dict[str, JSONValue] = {
        "mcp_tool": canonical_name,
        "is_error": result.isError,
    }
    if result.structuredContent is not None:
        details["structured_content"] = cast(
            JSONValue,
            result.structuredContent,
        )
    return AgentToolResult(content=content, details=details)


def _create_tool(
    *,
    client: MCPConnection,
    tool: types.Tool,
    tau_name: str,
    policy: MCPToolPolicy,
) -> AgentTool:
    canonical_name = tool.name
    display_name = policy.display_name
    annotations = tool.annotations
    execution_mode: Literal["sequential", "parallel"] = (
        "parallel"
        if annotations is not None
        and bool(getattr(annotations, "readOnlyHint", False))
        and bool(getattr(annotations, "idempotentHint", False))
        else "sequential"
    )
    private_meta = policy.private_meta(tool) if policy.private_meta is not None else None

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, on_update
        if signal is not None and signal.is_cancelled():
            return AgentToolResult(
                content=[TextContent(text=f"{display_name} MCP tool call was cancelled.")],
                details={"mcp_tool": canonical_name, "cancelled": True},
            )
        refusal = policy.refusal(tool) if policy.refusal is not None else None
        if refusal is not None:
            return AgentToolResult(
                content=[TextContent(text=refusal)],
                details={"mcp_tool": canonical_name, "is_error": True, "refused": True},
            )
        if policy.check_arguments is not None:
            policy.check_arguments(canonical_name, arguments)
        if private_meta is None:
            result = await client.call_tool(canonical_name, dict(arguments))
        else:
            result = await client.call_tool(
                canonical_name,
                dict(arguments),
                meta=dict(private_meta),
            )
        return mcp_tool_result(
            canonical_name=canonical_name,
            result=result,
            display_name=display_name,
        )

    parameters = (
        policy.input_schema(tool)
        if policy.input_schema is not None
        else cast(Mapping[str, JSONValue], deepcopy(tool.inputSchema))
    )
    return AgentTool(
        name=tau_name,
        label=_tool_label(tool),
        description=tool.description or f"Call {display_name} MCP tool {canonical_name}.",
        parameters=parameters,
        execute_fn=execute,
        execution_mode=execution_mode,
    )


def _create_resource_tool(client: MCPConnection, policy: MCPToolPolicy) -> AgentTool:
    display_name = policy.display_name
    resources = {str(resource.uri): resource for resource in client.resources}

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, on_update
        uri = str(arguments.get("uri") or "")
        if signal is not None and signal.is_cancelled():
            return AgentToolResult(
                content=[TextContent(text=f"{display_name} MCP resource read was cancelled.")],
                details={"uri": uri, "cancelled": True},
            )
        if uri not in resources:
            return AgentToolResult(
                content=[TextContent(text=f"Unknown {display_name} MCP resource URI.")],
                details={"uri": uri, "is_error": True},
            )
        result = await client.read_resource(uri)
        content: list[TextContent | ImageContent] = []
        for item in result.contents:
            if isinstance(item, types.TextResourceContents):
                content.append(TextContent(text=item.text))
            else:
                content.append(
                    TextContent(
                        text=_json_text(item.model_dump(mode="json")),
                    )
                )
        return AgentToolResult(
            content=content,
            details={"uri": uri, "is_error": False},
        )

    return AgentTool(
        name=mcp_resource_tool_name(policy.prefix),
        label=f"Read {display_name} Resource",
        description=policy.resource_tool_description
        or f"Read one advertised {display_name} resource from MCP.",
        parameters={
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "enum": list(resources),
                    "description": f"An advertised {display_name} MCP resource URI.",
                }
            },
            "required": ["uri"],
            "additionalProperties": False,
        },
        execute_fn=execute,
        execution_mode="parallel",
    )


def create_mcp_connection_tools(
    client: MCPConnection,
    policy: MCPToolPolicy,
) -> list[AgentTool]:
    """Return one Tau tool per offered MCP tool, plus a resource reader when resources exist."""

    tools: list[AgentTool] = []
    names: set[str] = set()
    resource_tool_name = mcp_resource_tool_name(policy.prefix)
    for tool in client.tools:
        if tool.name in policy.excluded_tools:
            continue
        tau_name = mcp_tool_name(policy.prefix, tool.name)
        if tau_name in names or tau_name == resource_tool_name:
            raise ValueError(f"{policy.display_name} MCP tool name collision: {tool.name}")
        names.add(tau_name)
        tools.append(_create_tool(client=client, tool=tool, tau_name=tau_name, policy=policy))
    if client.resources:
        tools.append(_create_resource_tool(client, policy))
    return tools
