"""The Main Sequence platform's own MCP connection: its policy for offering tools to Tau."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import cast

from mcp import types
from tau_agent.tools import AgentTool
from tau_agent.types import JSONValue

from ms_tau_sdk.backend.mcp import (
    ENVIRONMENT_SCOPED_AGENT_TOOLS,
    ENVIRONMENT_UID_ARGUMENT,
)
from ms_tau_sdk.runtime.requester import _current_turn_binding
from ms_tau_sdk.tools.mcp_connection import (
    MCPConnection,
    MCPToolPolicy,
    create_mcp_connection_tools,
    mcp_resource_tool_name,
)

_PREFIX = "mainsequence"
_DISPLAY_NAME = "Main Sequence"
_RESOURCE_TOOL_NAME = mcp_resource_tool_name(_PREFIX)
# Every hosted call names the session the turn works on (ADR 0021). It never asks for a person.
CALLER_SESSION_PROOF_META_KEY = "mainsequence.ai/caller-session-proof/v1"
# A hosted call made while the turn serves a person also carries that person's delegation: the
# same session proof, from which the platform finds the person. Without it the call is the
# Agent's own, and the tool decides what it may do.
DELEGATION_META_KEY = "mainsequence.ai/delegation/v1"
A2A_SEND_TOOL = "a2a.send_message"
# These operations return credentials for direct-runtime clients. Tau never calls them for
# the model: agent-to-agent turns use a2a.send_message, so the credential stays out of model
# content, tool details, events, and persisted history.
CREDENTIAL_RESULT_MCP_TOOLS = frozenset({"agent_session.resolve_runtime_access"})


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


def _turn_envelope(session_uid: str) -> dict[str, JSONValue]:
    """The private metadata of one hosted call: the session, and the delegation when serving."""

    binding = _current_turn_binding()
    if binding is None or binding.session_uid != session_uid:
        raise RuntimeError("Main Sequence MCP tools run only inside their session's turn")
    proof = binding.session_proof()
    session: dict[str, JSONValue] = {
        "caller_agent_session_uid": proof.session_uid,
        "lease_holder_id": proof.holder_id,
        "lease_token": proof.lease_token,
    }
    envelope: dict[str, JSONValue] = {CALLER_SESSION_PROOF_META_KEY: session}
    if binding.serving:
        envelope[DELEGATION_META_KEY] = dict(session)
    return envelope


def _check_arguments(
    canonical_name: str,
    arguments: Mapping[str, JSONValue],
    *,
    local_user_semantics: bool,
) -> None:
    if canonical_name != A2A_SEND_TOOL:
        return
    response_kind = arguments.get("response_kind")
    completion_policy = arguments.get("completion_policy")
    if response_kind not in {"message", "task"}:
        raise ValueError("a2a.send_message requires response_kind 'message' or 'task'")
    if response_kind == "task" and completion_policy not in {
        "poll",
        "resume_caller",
    }:
        raise ValueError("Task response_kind requires completion_policy 'poll' or 'resume_caller'")
    if local_user_semantics and completion_policy == "resume_caller":
        raise ValueError("Local A2A Task communication requires completion_policy 'poll'")
    if response_kind == "message" and completion_policy is not None:
        raise ValueError("completion_policy is not valid for Message response_kind")


def mainsequence_mcp_policy(*, session_uid: str | None = None) -> MCPToolPolicy:
    """The platform connection's policy: hidden tools, A2A rules, and the private envelope.

    ``session_uid`` is the hosted session whose turns call the tools: every call names it, and
    carries the delegation while the turn serves a person. In local mode it is None, the calls
    carry no private metadata, and A2A Tasks are polled.
    """

    local_user_semantics = session_uid is None
    return MCPToolPolicy(
        prefix=_PREFIX,
        display_name=_DISPLAY_NAME,
        excluded_tools=CREDENTIAL_RESULT_MCP_TOOLS,
        input_schema=lambda tool: _tau_tool_input_schema(
            tool,
            local_user_semantics=local_user_semantics,
        ),
        check_arguments=lambda name, arguments: _check_arguments(
            name,
            arguments,
            local_user_semantics=local_user_semantics,
        ),
        private_meta=(
            (lambda _tool: _turn_envelope(session_uid)) if session_uid is not None else None
        ),
        resource_tool_description="Read one advertised Main Sequence platform resource from MCP.",
    )


def create_mainsequence_mcp_tools(
    client: MCPConnection,
    *,
    session_uid: str | None = None,
) -> list[AgentTool]:
    return create_mcp_connection_tools(client, mainsequence_mcp_policy(session_uid=session_uid))


def mainsequence_mcp_resource_prompt(
    client: MCPConnection,
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
