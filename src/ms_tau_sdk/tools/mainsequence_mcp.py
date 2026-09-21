"""Adapt Main Sequence MCP tools and resources to Tau."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Literal, cast

from mcp import types
from tau_agent.messages import ImageContent, TextContent
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue

from ms_tau_sdk.backend.mcp import (
    ENVIRONMENT_SCOPED_AGENT_TOOLS,
    ENVIRONMENT_UID_ARGUMENT,
    MainSequenceMCPClient,
)

_INVALID_TOOL_NAME = re.compile(r"[^A-Za-z0-9_-]")
_RESOURCE_TOOL_NAME = "mainsequence__read_resource"
CALLER_SESSION_PROOF_META_KEY = "mainsequence.ai/caller-session-proof/v1"
CALLER_SESSION_PROOF_REQUIRED_META_KEY = "mainsequence.ai/requires-caller-session-proof/v1"
A2A_SEND_TOOL = "a2a.send_message"


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
            content.append(TextContent(text=_json_text(block.model_dump(mode="json"))))
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


def _tau_tool_input_schema(
    tool: types.Tool,
    *,
    local_user_semantics: bool = False,
) -> Mapping[str, JSONValue]:
    schema = deepcopy(tool.inputSchema)
    if tool.name == A2A_SEND_TOOL:
        properties = schema.setdefault("properties", {})
        if isinstance(properties, dict):
            properties["response_kind"] = {
                "type": "string",
                "enum": ["message", "task"],
                "description": "Required result shape; it is never inferred.",
            }
            properties["completion_policy"] = {
                "type": "string",
                "enum": ["poll"] if local_user_semantics else ["poll", "resume_caller"],
                "description": "Required only for Task mode.",
            }
        required = schema.setdefault("required", [])
        if isinstance(required, list) and "response_kind" not in required:
            required.append("response_kind")
        all_of = schema.setdefault("allOf", [])
        if isinstance(all_of, list):
            all_of.extend(
                [
                    {
                        "if": {
                            "properties": {"response_kind": {"const": "task"}},
                            "required": ["response_kind"],
                        },
                        "then": {"required": ["completion_policy"]},
                    },
                    {
                        "if": {
                            "properties": {"response_kind": {"const": "message"}},
                            "required": ["response_kind"],
                        },
                        "then": {"not": {"required": ["completion_policy"]}},
                    },
                ]
            )
    if tool.name not in ENVIRONMENT_SCOPED_AGENT_TOOLS:
        return cast(Mapping[str, JSONValue], schema)

    properties = schema.get("properties")
    if isinstance(properties, dict):
        properties.pop(ENVIRONMENT_UID_ARGUMENT, None)
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [
            field_name for field_name in required if field_name != ENVIRONMENT_UID_ARGUMENT
        ]
    return cast(Mapping[str, JSONValue], schema)


def _caller_session_meta(
    tool: types.Tool,
    proof: Mapping[str, JSONValue] | None,
    *,
    allow_missing_proof: bool = False,
) -> Mapping[str, JSONValue] | None:
    tool_meta = tool.meta or {}
    if tool_meta.get(CALLER_SESSION_PROOF_REQUIRED_META_KEY) is not True:
        return None
    if proof is None:
        if allow_missing_proof:
            return None
        raise ValueError(f"Main Sequence MCP tool requires caller-session proof: {tool.name}")
    return {CALLER_SESSION_PROOF_META_KEY: dict(proof)}


def _create_mcp_tool(
    *,
    client: MainSequenceMCPClient,
    tool: types.Tool,
    tau_name: str,
    private_meta: Mapping[str, JSONValue] | None = None,
    local_user_semantics: bool = False,
) -> AgentTool:
    canonical_name = tool.name
    annotations = tool.annotations
    execution_mode: Literal["sequential", "parallel"] = (
        "parallel"
        if annotations is not None
        and bool(getattr(annotations, "readOnlyHint", False))
        and bool(getattr(annotations, "idempotentHint", False))
        else "sequential"
    )

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
        if canonical_name == A2A_SEND_TOOL:
            response_kind = arguments.get("response_kind")
            completion_policy = arguments.get("completion_policy")
            if response_kind not in {"message", "task"}:
                raise ValueError("a2a.send_message requires response_kind 'message' or 'task'")
            if response_kind == "task" and completion_policy not in {
                "poll",
                "resume_caller",
            }:
                raise ValueError(
                    "Task response_kind requires completion_policy 'poll' or 'resume_caller'"
                )
            if local_user_semantics and completion_policy == "resume_caller":
                raise ValueError("Local A2A Task communication requires completion_policy 'poll'")
            if response_kind == "message" and completion_policy is not None:
                raise ValueError("completion_policy is not valid for Message response_kind")
        if private_meta is None:
            result = await client.call_tool(canonical_name, dict(arguments))
        else:
            result = await client.call_tool(
                canonical_name,
                dict(arguments),
                meta=dict(private_meta),
            )
        return _tool_result(canonical_name=canonical_name, result=result)

    return AgentTool(
        name=tau_name,
        label=_tool_label(tool),
        description=tool.description or f"Call Main Sequence MCP tool {canonical_name}.",
        parameters=_tau_tool_input_schema(
            tool,
            local_user_semantics=local_user_semantics,
        ),
        execute_fn=execute,
        execution_mode=execution_mode,
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
    *,
    caller_session_proof: Mapping[str, JSONValue] | None = None,
    allow_missing_session_proof: bool = False,
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
                private_meta=_caller_session_meta(
                    tool,
                    caller_session_proof,
                    allow_missing_proof=allow_missing_session_proof,
                ),
                local_user_semantics=allow_missing_session_proof,
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
        (f"Use `{_RESOURCE_TOOL_NAME}` to read a resource when its guidance is relevant."),
    ]
    for resource in client.resources:
        description = f": {resource.description}" if resource.description else ""
        lines.append(f"- `{resource.uri}`{description}")
    return "\n".join(lines)
