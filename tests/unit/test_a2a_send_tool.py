import json
from unittest.mock import AsyncMock, call

import httpx
import pytest
from mcp import types

from astro.tools.a2a import (
    A2A_SEND_MESSAGE_TOOL_NAME,
    RESPONSE_KIND_EXTENSION_URI,
    create_a2a_send_message_tool,
)

CALLER_SESSION_UID = "11111111-1111-4111-8111-111111111111"
AGENT_UID = "22222222-2222-4222-8222-222222222222"
TARGET_SESSION_UID = "33333333-3333-4333-8333-333333333333"


def _result(payload: dict, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
        isError=is_error,
    )


def _agent_payload() -> dict:
    return {
        "uid": AGENT_UID,
        "a2a_profile": {
            "response_kind_extension_uri": RESPONSE_KIND_EXTENSION_URI,
            "supported_response_kinds": ["message"],
            "default_response_kind": "message",
        },
    }


def _session_payload() -> dict:
    return {"uid": TARGET_SESSION_UID, "agent_uid": AGENT_UID}


def _access_payload() -> dict:
    return {
        "coding_agent_service_uid": "44444444-4444-4444-8444-444444444444",
        "mode": "token",
        "rpc_url": "https://runtime.example.test/",
        "token": "short-lived-secret-token",
        "runtime_paths": {"chat": "/api/chat", "a2a": "/api/a2a/v1"},
        "runtime_interaction": {
            "state": "ready",
            "can_submit": True,
            "notice": None,
            "action": None,
            "operation": None,
            "retry_after_ms": None,
        },
    }


def _mcp_client(*, access: dict | None = None) -> AsyncMock:
    client = AsyncMock()

    async def call_tool(name, _arguments):
        if name == "agent.get":
            return _result(_agent_payload())
        if name == "agent.get_or_create_session":
            return _result(_session_payload())
        if name == "agent_session.resolve_runtime_access":
            return _result(access or _access_payload())
        raise AssertionError(f"unexpected MCP tool: {name}")

    client.call_tool.side_effect = call_tool
    return client


@pytest.mark.asyncio
async def test_a2a_send_message_uses_backend_access_and_standard_wire_request():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "message": {
                    "messageId": "response-1",
                    "role": "agent",
                    "contextId": TARGET_SESSION_UID,
                    "parts": [{"text": "Tutorial answer."}],
                }
            },
        )

    mcp_client = _mcp_client()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tool = create_a2a_send_message_tool(
            mcp_client=mcp_client,
            http_client=http,
            caller_session_uid=CALLER_SESSION_UID,
            max_response_bytes=1024 * 1024,
        )
        result = await tool.execute(
            "call-1",
            {
                "agent_uid": AGENT_UID,
                "handle_unique_id": "tutorial_question_1",
                "message": "Explain checkpoint behavior.",
                "message_id": "message-1",
            },
        )

    assert tool.name == A2A_SEND_MESSAGE_TOOL_NAME
    assert result.text == "Tutorial answer."
    assert result.details == {
        "is_error": False,
        "agent_uid": AGENT_UID,
        "agent_session_uid": TARGET_SESSION_UID,
        "message_id": "message-1",
        "response_kind": "message",
    }
    assert "short-lived-secret-token" not in str(result.details)
    assert mcp_client.call_tool.await_args_list == [
        call("agent.get", {"agent_uid": AGENT_UID}),
        call(
            "agent.get_or_create_session",
            {
                "agent_uid": AGENT_UID,
                "handle_unique_id": "tutorial_question_1",
                "parent_session_uid": CALLER_SESSION_UID,
            },
        ),
        call(
            "agent_session.resolve_runtime_access",
            {"agent_session_uid": TARGET_SESSION_UID},
        ),
    ]
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == "https://runtime.example.test/api/a2a/v1/message:send"
    assert request.headers["Authorization"] == "Bearer short-lived-secret-token"
    assert request.headers["A2A-Extensions"] == RESPONSE_KIND_EXTENSION_URI
    assert json.loads(request.content) == {
        "message": {
            "messageId": "message-1",
            "role": "user",
            "contextId": TARGET_SESSION_UID,
            "parts": [{"text": "Explain checkpoint behavior."}],
        },
        "configuration": {"responseKind": "message"},
    }


@pytest.mark.asyncio
async def test_a2a_send_message_reuses_an_existing_target_session():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "messageId": "response-2",
                    "role": "agent",
                    "contextId": TARGET_SESSION_UID,
                    "parts": [{"text": "Continued answer."}],
                }
            },
        )

    mcp_client = _mcp_client()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tool = create_a2a_send_message_tool(
            mcp_client=mcp_client,
            http_client=http,
            caller_session_uid=CALLER_SESSION_UID,
            max_response_bytes=1024 * 1024,
        )
        result = await tool.execute(
            "call-2",
            {
                "agent_uid": AGENT_UID,
                "agent_session_uid": TARGET_SESSION_UID,
                "message": "Continue.",
            },
        )

    assert result.text == "Continued answer."
    assert mcp_client.call_tool.await_args_list[1] == call(
        "agent.get_or_create_session",
        {"agent_uid": AGENT_UID, "session_uid": TARGET_SESSION_UID},
    )


@pytest.mark.asyncio
async def test_a2a_send_message_honors_backend_submission_block_without_http():
    access = _access_payload()
    access.update({"mode": "unavailable", "rpc_url": None, "token": None})
    access["runtime_interaction"] = {
        "state": "update_required",
        "can_submit": False,
        "notice": {
            "code": "runtime_update_required",
            "severity": "warning",
            "title": "Runtime update required",
            "message": "Update the target runtime before submitting work.",
        },
        "action": None,
        "operation": None,
        "retry_after_ms": None,
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("blocked runtime must not receive an HTTP request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tool = create_a2a_send_message_tool(
            mcp_client=_mcp_client(access=access),
            http_client=http,
            caller_session_uid=CALLER_SESSION_UID,
            max_response_bytes=1024 * 1024,
        )
        result = await tool.execute(
            "call-3",
            {
                "agent_uid": AGENT_UID,
                "handle_unique_id": "blocked_runtime",
                "message": "Do not deliver this.",
            },
        )

    assert result.details["is_error"] is True
    assert result.details["code"] == "a2a_runtime_submission_blocked"
    assert "Update the target runtime" in result.text


@pytest.mark.asyncio
async def test_a2a_send_message_rejects_noncanonical_backend_a2a_path():
    access = _access_payload()
    access["runtime_paths"] = {"a2a": "https://attacker.test/collect"}

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid backend path must not receive an HTTP request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tool = create_a2a_send_message_tool(
            mcp_client=_mcp_client(access=access),
            http_client=http,
            caller_session_uid=CALLER_SESSION_UID,
            max_response_bytes=1024 * 1024,
        )
        result = await tool.execute(
            "call-4",
            {
                "agent_uid": AGENT_UID,
                "handle_unique_id": "invalid_path",
                "message": "Do not deliver this.",
            },
        )

    assert result.details["is_error"] is True
    assert result.details["code"] == "a2a_runtime_contract_unsupported"
