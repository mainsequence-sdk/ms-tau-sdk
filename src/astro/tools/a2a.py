"""Constrained outbound A2A communication for Tau sessions."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import urlsplit

import anyio
import httpx
import structlog
from mcp import types
from tau_agent.messages import TextContent
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue

from astro.backend.mcp import MainSequenceMCPClient
from astro.protocols.a2a_roles import (
    A2AMessageDirection,
    a2a_v1_wire_role,
    has_a2a_v1_direction,
)

logger = structlog.get_logger(__name__)

A2A_SEND_MESSAGE_TOOL_NAME = "mainsequence__a2a_send_message"
RESPONSE_KIND_EXTENSION_URI = "https://mainsequence.ai/a2a/extensions/response-kind/v1"
TAU_A2A_PATH = "/api/a2a/v1"
TRANSIENT_RUNTIME_INTERACTION_STATES = {
    "checking",
    "starting",
    "waking",
    "updating",
}

_INPUT_SCHEMA: Mapping[str, JSONValue] = {
    "type": "object",
    "properties": {
        "agent_uid": {
            "type": "string",
            "description": "Public UID of the selected target Agent.",
        },
        "message": {
            "type": "string",
            "minLength": 1,
            "description": "Bounded request to send to the target Agent.",
        },
        "handle_unique_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 255,
            "pattern": "^[-a-zA-Z0-9_]+$",
            "description": (
                "Stable task-specific handle used to create or reuse the target "
                "conversation. Required unless agent_session_uid is supplied."
            ),
        },
        "agent_session_uid": {
            "type": "string",
            "description": (
                "Existing target AgentSession UID for a continuation. Do not combine "
                "with handle_unique_id."
            ),
        },
        "message_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": "^[A-Za-z0-9._:-]+$",
            "description": (
                "Stable A2A message identifier. Omit for a new message. Preserve the "
                "reported identifier when diagnosing an ambiguous transport outcome."
            ),
        },
    },
    "required": ["agent_uid", "message"],
    "oneOf": [
        {
            "required": ["handle_unique_id"],
            "not": {"required": ["agent_session_uid"]},
        },
        {
            "required": ["agent_session_uid"],
            "not": {"required": ["handle_unique_id"]},
        },
    ],
    "additionalProperties": False,
}


class _A2ACommunicationError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _cancelled(signal: ToolCancellationToken | None) -> None:
    if signal is not None and signal.is_cancelled():
        raise _A2ACommunicationError(
            "a2a_send_cancelled",
            "Outbound A2A communication was cancelled.",
        )


def _mcp_payload(result: types.CallToolResult, *, operation: str) -> dict[str, Any]:
    structured = result.structuredContent
    if result.isError:
        detail = f"Main Sequence MCP operation {operation} failed."
        if isinstance(structured, dict):
            body = structured.get("body")
            if isinstance(body, dict) and isinstance(body.get("detail"), str):
                detail = body["detail"]
        raise _A2ACommunicationError("a2a_platform_operation_failed", detail)
    if not isinstance(structured, dict):
        raise _A2ACommunicationError(
            "a2a_platform_contract_invalid",
            f"Main Sequence MCP operation {operation} returned no structured result.",
        )
    return structured


def _profile_supports_message(agent: dict[str, Any]) -> bool:
    profile = agent.get("a2a_profile")
    if not isinstance(profile, dict):
        return True
    if profile.get("response_kind_extension_uri") != RESPONSE_KIND_EXTENSION_URI:
        return False
    supported = profile.get("supported_response_kinds")
    return isinstance(supported, list) and "message" in supported


def _runtime_endpoint(access: dict[str, Any]) -> tuple[str, str]:
    interaction = access.get("runtime_interaction")
    if not isinstance(interaction, dict) or interaction.get("can_submit") is not True:
        notice = interaction.get("notice") if isinstance(interaction, dict) else None
        detail = access.get("detail")
        if isinstance(notice, dict) and isinstance(notice.get("message"), str):
            detail = notice["message"]
        if not isinstance(detail, str) or not detail.strip():
            detail = "The target runtime is not accepting new messages."
        raise _A2ACommunicationError("a2a_runtime_submission_blocked", detail)

    rpc_url = access.get("rpc_url")
    token = access.get("token")
    paths = access.get("runtime_paths")
    if (
        access.get("mode") != "token"
        or not isinstance(rpc_url, str)
        or not rpc_url.strip()
        or not isinstance(token, str)
        or not token.strip()
        or not isinstance(paths, dict)
    ):
        raise _A2ACommunicationError(
            "a2a_runtime_access_unavailable",
            "The target runtime did not provide usable token access.",
        )

    parsed = urlsplit(rpc_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise _A2ACommunicationError(
            "a2a_runtime_access_invalid",
            "The target runtime returned an invalid access endpoint.",
        )
    a2a_path = paths.get("a2a")
    if a2a_path != TAU_A2A_PATH:
        raise _A2ACommunicationError(
            "a2a_runtime_contract_unsupported",
            "The target runtime does not advertise the supported Tau A2A path.",
        )
    endpoint = f"{rpc_url.rstrip('/')}{TAU_A2A_PATH}/message:send"
    return endpoint, token


async def _resolve_runtime_access(
    *,
    mcp_client: MainSequenceMCPClient,
    target_session_uid: str,
    signal: ToolCancellationToken | None,
    on_update: ToolUpdateCallback | None,
) -> dict[str, Any]:
    """Follow only backend-declared transient access states."""

    last_operation_uid = ""
    while True:
        _cancelled(signal)
        access = _mcp_payload(
            await mcp_client.call_tool(
                "agent_session.resolve_runtime_access",
                {"agent_session_uid": target_session_uid},
            ),
            operation="agent_session.resolve_runtime_access",
        )
        interaction = access.get("runtime_interaction")
        if not isinstance(interaction, dict) or interaction.get("can_submit") is True:
            return access
        state = str(interaction.get("state") or "")
        retry_after_ms = interaction.get("retry_after_ms")
        if state not in TRANSIENT_RUNTIME_INTERACTION_STATES or not isinstance(
            retry_after_ms, (int, float)
        ):
            return access

        operation = interaction.get("operation")
        operation_uid = (
            str(operation.get("uid") or "") if isinstance(operation, dict) else ""
        )
        if on_update is not None and operation_uid != last_operation_uid:
            notice = interaction.get("notice")
            detail = (
                str(notice.get("message") or "")
                if isinstance(notice, dict)
                else ""
            )
            on_update(
                AgentToolResult(
                    content=[
                        TextContent(
                            text=detail or "Waiting for the target runtime..."
                        )
                    ],
                    details={
                        "phase": "runtime_access",
                        "state": state,
                        "agent_session_uid": target_session_uid,
                        "operation_uid": operation_uid or None,
                    },
                )
            )
            last_operation_uid = operation_uid
        await anyio.sleep(min(30.0, max(0.5, float(retry_after_ms) / 1000)))


async def _bounded_json_response(
    response: httpx.Response,
    *,
    max_response_bytes: int,
) -> dict[str, Any]:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_response_bytes:
            raise _A2ACommunicationError(
                "a2a_response_too_large",
                "The target Agent response exceeded the configured response limit.",
            )
        chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _A2ACommunicationError(
            "a2a_response_invalid",
            "The target runtime returned an invalid A2A JSON response.",
        ) from error
    if not isinstance(payload, dict):
        raise _A2ACommunicationError(
            "a2a_response_invalid",
            "The target runtime returned an invalid A2A response shape.",
        )
    return cast(dict[str, Any], payload)


def _message_text(payload: dict[str, Any], *, session_uid: str) -> str:
    message = payload.get("message")
    if not isinstance(message, dict):
        raise _A2ACommunicationError(
            "a2a_response_kind_mismatch",
            "The target runtime did not return the requested message result.",
        )
    message_id = message.get("messageId")
    if "kind" in message or not isinstance(message_id, str) or not message_id.strip():
        raise _A2ACommunicationError(
            "a2a_response_invalid",
            "The target runtime returned an invalid A2A v1 Message envelope.",
        )
    if message.get("contextId") != session_uid or not has_a2a_v1_direction(
        message,
        A2AMessageDirection.RESPONDER,
    ):
        raise _A2ACommunicationError(
            "a2a_response_invalid",
            "The target runtime returned a message for the wrong context or role.",
        )
    parts = message.get("parts")
    if not isinstance(parts, list):
        raise _A2ACommunicationError(
            "a2a_response_invalid",
            "The target Agent message did not contain valid parts.",
        )
    text = "\n".join(
        str(part["text"])
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ).strip()
    if not text:
        raise _A2ACommunicationError(
            "a2a_response_empty",
            "The target Agent returned an empty message.",
        )
    return text


def _error_result(
    error: _A2ACommunicationError,
    *,
    agent_uid: str,
    session_uid: str | None,
    message_id: str,
) -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text=f"A2A communication failed: {error.detail}")],
        details={
            "is_error": True,
            "code": error.code,
            "agent_uid": agent_uid,
            "agent_session_uid": session_uid,
            "message_id": message_id,
        },
    )


def create_a2a_send_message_tool(
    *,
    mcp_client: MainSequenceMCPClient,
    http_client: httpx.AsyncClient,
    caller_session_uid: str,
    max_response_bytes: int,
) -> AgentTool:
    """Create a message-only A2A tool with backend-mediated target access."""

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id
        agent_uid = str(arguments.get("agent_uid") or "").strip()
        outbound_message = str(arguments.get("message") or "").strip()
        existing_session_uid = str(arguments.get("agent_session_uid") or "").strip()
        handle_unique_id = str(arguments.get("handle_unique_id") or "").strip()
        message_id = str(arguments.get("message_id") or uuid.uuid4())
        target_session_uid: str | None = existing_session_uid or None
        started_at = time.monotonic()

        try:
            _cancelled(signal)
            if not agent_uid or not outbound_message:
                raise _A2ACommunicationError(
                    "a2a_request_invalid",
                    "agent_uid and a non-empty message are required.",
                )
            if bool(existing_session_uid) == bool(handle_unique_id):
                raise _A2ACommunicationError(
                    "a2a_request_invalid",
                    "Provide exactly one of agent_session_uid or handle_unique_id.",
                )

            agent = _mcp_payload(
                await mcp_client.call_tool("agent.get", {"agent_uid": agent_uid}),
                operation="agent.get",
            )
            if not _profile_supports_message(agent):
                raise _A2ACommunicationError(
                    "a2a_response_kind_unsupported",
                    "The selected Agent does not advertise message responses.",
                )
            _cancelled(signal)

            session_arguments: dict[str, object] = {"agent_uid": agent_uid}
            if existing_session_uid:
                session_arguments["session_uid"] = existing_session_uid
            else:
                session_arguments.update(
                    {
                        "handle_unique_id": handle_unique_id,
                        "parent_session_uid": caller_session_uid,
                    }
                )
            session = _mcp_payload(
                await mcp_client.call_tool(
                    "agent.get_or_create_session",
                    session_arguments,
                ),
                operation="agent.get_or_create_session",
            )
            target_session_uid = str(session.get("uid") or "").strip()
            if not target_session_uid or session.get("agent_uid") != agent_uid:
                raise _A2ACommunicationError(
                    "a2a_session_contract_invalid",
                    "The platform returned an invalid target AgentSession.",
                )
            _cancelled(signal)

            access = await _resolve_runtime_access(
                mcp_client=mcp_client,
                target_session_uid=target_session_uid,
                signal=signal,
                on_update=on_update,
            )
            endpoint, token = _runtime_endpoint(access)
            _cancelled(signal)

            if on_update is not None:
                on_update(
                    AgentToolResult(
                        content=[TextContent(text="Sending the A2A message...")],
                        details={
                            "phase": "send",
                            "agent_uid": agent_uid,
                            "agent_session_uid": target_session_uid,
                            "message_id": message_id,
                        },
                    )
                )
            logger.info(
                "a2a.outbound.started",
                caller_agent_session_uid=caller_session_uid,
                target_agent_uid=agent_uid,
                target_agent_session_uid=target_session_uid,
                a2a_message_id=message_id,
                response_kind="message",
            )
            async with http_client.stream(
                "POST",
                endpoint,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/a2a+json",
                    "Content-Type": "application/a2a+json",
                    "A2A-Extensions": RESPONSE_KIND_EXTENSION_URI,
                },
                json={
                    "message": {
                        "messageId": message_id,
                        "role": a2a_v1_wire_role(A2AMessageDirection.REQUESTER),
                        "contextId": target_session_uid,
                        "parts": [{"text": outbound_message}],
                    },
                    "configuration": {"responseKind": "message"},
                },
            ) as response:
                payload = await _bounded_json_response(
                    response,
                    max_response_bytes=max_response_bytes,
                )
                if not response.is_success:
                    detail = payload.get("detail")
                    if not isinstance(detail, str) or not detail.strip():
                        detail = f"Target runtime returned HTTP {response.status_code}."
                    raise _A2ACommunicationError("a2a_runtime_rejected", detail)
            text = _message_text(payload, session_uid=target_session_uid)
            logger.info(
                "a2a.outbound.completed",
                caller_agent_session_uid=caller_session_uid,
                target_agent_uid=agent_uid,
                target_agent_session_uid=target_session_uid,
                a2a_message_id=message_id,
                response_kind="message",
                outcome="success",
                duration_ms=round((time.monotonic() - started_at) * 1000, 3),
            )
            return AgentToolResult(
                content=[TextContent(text=text)],
                details={
                    "is_error": False,
                    "agent_uid": agent_uid,
                    "agent_session_uid": target_session_uid,
                    "message_id": message_id,
                    "response_kind": "message",
                },
            )
        except httpx.TransportError:
            error = _A2ACommunicationError(
                "a2a_transport_outcome_ambiguous",
                (
                    "The direct target-runtime connection failed or disconnected. The turn "
                    "may have executed; do not automatically resend it."
                ),
            )
        except _A2ACommunicationError as caught:
            error = caught
        except Exception:
            logger.exception(
                "a2a.outbound.failed",
                caller_agent_session_uid=caller_session_uid,
                target_agent_uid=agent_uid,
                target_agent_session_uid=target_session_uid,
                a2a_message_id=message_id,
                outcome="error",
            )
            error = _A2ACommunicationError(
                "a2a_internal_error",
                "Outbound A2A communication failed unexpectedly.",
            )

        logger.warning(
            "a2a.outbound.completed",
            caller_agent_session_uid=caller_session_uid,
            target_agent_uid=agent_uid,
            target_agent_session_uid=target_session_uid,
            a2a_message_id=message_id,
            response_kind="message",
            outcome="error",
            error_code=error.code,
            duration_ms=round((time.monotonic() - started_at) * 1000, 3),
        )
        return _error_result(
            error,
            agent_uid=agent_uid,
            session_uid=target_session_uid,
            message_id=message_id,
        )

    return AgentTool(
        name=A2A_SEND_MESSAGE_TOOL_NAME,
        label="Send A2A Message",
        description=(
            "Send one message directly to a selected Main Sequence Agent. This host tool "
            "creates or reuses the target AgentSession, resolves fresh runtime access, "
            "keeps the short-lived credential internal, and calls the standard A2A "
            "message endpoint. Use it after agent.search instead of a generic HTTP tool."
        ),
        parameters=_INPUT_SCHEMA,
        execute_fn=execute,
        execution_mode="sequential",
    )


__all__ = ["A2A_SEND_MESSAGE_TOOL_NAME", "create_a2a_send_message_tool"]
