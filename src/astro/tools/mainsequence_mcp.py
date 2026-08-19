"""Adapt Main Sequence MCP tools and resources to Tau."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import cast

from mcp import types
from tau_agent.messages import ImageContent, TextContent
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue

from astro.backend.mcp import (
    ENVIRONMENT_SCOPED_AGENT_TOOLS,
    ENVIRONMENT_UID_ARGUMENT,
    MainSequenceMCPClient,
)

_INVALID_TOOL_NAME = re.compile(r"[^A-Za-z0-9_-]")
_RESOURCE_TOOL_NAME = "mainsequence__read_resource"


def _tau_tool_name(mcp_name: str) -> str:
    normalized = _INVALID_TOOL_NAME.sub("_", mcp_name)
    return f"mainsequence__{normalized}"


def _tool_label(tool: types.Tool) -> str:
    if tool.annotations is not None and tool.annotations.title:
        return tool.annotations.title
    return tool.title or tool.name


def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _tool_result(
    *,
    canonical_name: str,
    result: types.CallToolResult,
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
            content.append(
                TextContent(text=_json_text(block.model_dump(mode="json")))
            )
    if not content and result.structuredContent is not None:
        content.append(TextContent(text=_json_text(result.structuredContent)))
    if not content and result.isError:
        content.append(TextContent(text=f"Main Sequence MCP tool {canonical_name} failed."))

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


def _tau_tool_input_schema(tool: types.Tool) -> Mapping[str, JSONValue]:
    schema = deepcopy(tool.inputSchema)
    if tool.name not in ENVIRONMENT_SCOPED_AGENT_TOOLS:
        return cast(Mapping[str, JSONValue], schema)

    properties = schema.get("properties")
    if isinstance(properties, dict):
        properties.pop(ENVIRONMENT_UID_ARGUMENT, None)
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [
            field_name
            for field_name in required
            if field_name != ENVIRONMENT_UID_ARGUMENT
        ]
    return cast(Mapping[str, JSONValue], schema)


def _create_mcp_tool(
    *,
    client: MainSequenceMCPClient,
    tool: types.Tool,
    tau_name: str,
) -> AgentTool:
    canonical_name = tool.name

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, on_update
        if signal is not None and signal.is_cancelled():
            return AgentToolResult(
                content=[TextContent(text="Main Sequence MCP tool call was cancelled.")],
                details={"mcp_tool": canonical_name, "cancelled": True},
            )
        result = await client.call_tool(canonical_name, dict(arguments))
        return _tool_result(canonical_name=canonical_name, result=result)

    return AgentTool(
        name=tau_name,
        label=_tool_label(tool),
        description=tool.description or f"Call Main Sequence MCP tool {canonical_name}.",
        parameters=_tau_tool_input_schema(tool),
        execute_fn=execute,
        execution_mode="sequential",
    )


def _create_resource_tool(client: MainSequenceMCPClient) -> AgentTool:
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
                content=[TextContent(text="Main Sequence MCP resource read was cancelled.")],
                details={"uri": uri, "cancelled": True},
            )
        if uri not in resources:
            return AgentToolResult(
                content=[TextContent(text="Unknown Main Sequence MCP resource URI.")],
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
        name=_RESOURCE_TOOL_NAME,
        label="Read Main Sequence Resource",
        description="Read one advertised Main Sequence platform resource from MCP.",
        parameters={
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "enum": list(resources),
                    "description": "An advertised Main Sequence MCP resource URI.",
                }
            },
            "required": ["uri"],
            "additionalProperties": False,
        },
        execute_fn=execute,
        execution_mode="parallel",
    )


def create_mainsequence_mcp_tools(
    client: MainSequenceMCPClient,
) -> list[AgentTool]:
    tools: list[AgentTool] = []
    names: set[str] = set()
    for tool in client.tools:
        tau_name = _tau_tool_name(tool.name)
        if tau_name in names or tau_name == _RESOURCE_TOOL_NAME:
            raise ValueError(f"Main Sequence MCP tool name collision: {tool.name}")
        names.add(tau_name)
        tools.append(
            _create_mcp_tool(
                client=client,
                tool=tool,
                tau_name=tau_name,
            )
        )
    if client.resources:
        tools.append(_create_resource_tool(client))
    return tools


def mainsequence_mcp_resource_prompt(
    client: MainSequenceMCPClient,
) -> str:
    if not client.resources:
        return ""
    lines = [
        "# Main Sequence platform resources",
        (
            f"Use `{_RESOURCE_TOOL_NAME}` to read a resource when its guidance "
            "is relevant."
        ),
    ]
    for resource in client.resources:
        description = f": {resource.description}" if resource.description else ""
        lines.append(f"- `{resource.uri}`{description}")
    return "\n".join(lines)
