import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from mcp import types

from ms_tau_sdk.runtime.task_context import TaskExecutionContext, task_execution_scope
from ms_tau_sdk.tools.mainsequence_mcp import create_mainsequence_mcp_tools
from ms_tau_sdk.tools.secret_entry import secret_entry_result


def payload(**changes):
    uid = "bdac7952-ea64-4eb0-8158-6977e8d5ed62"
    env = "8992caa6-e7b6-4bb5-8a1b-9eaa1a676740"
    return {
        "uid": uid,
        "status": "pending",
        "operation": "create",
        "name": "SERVICE_KEY",
        "purpose": "Connect integration",
        "organization_environment_uid": env,
        "organization_environment_name": "development",
        "requesting_agent_name": "Example",
        "consumer_user_uid": None,
        "secret_uid": None,
        "entry_url": f"https://ui.example.test/app/main-sequence-foundry/secrets?secretEntryRequest={uid}&organization_environment_uid={env}",
        "expires_at": "2026-10-06T16:00:00Z",
        "error_code": "",
        "requirement_reference": f"secret-entry:{uid}",
        **changes,
    }


def response(**changes):
    return types.CallToolResult(
        content=[types.TextContent(type="text", text="UNTRUSTED_FREE_TEXT_MUST_NOT_PROPAGATE")],
        structuredContent=payload(**changes),
    )


def client():
    result = AsyncMock()
    result.tools = (
        types.Tool(
            name="secret_entry.start",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "purpose": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                },
                "required": ["name", "purpose", "idempotency_key"],
                "additionalProperties": False,
            },
        ),
    )
    result.resources = ()
    result.call_tool.return_value = response()
    return result


def test_only_validated_status_enters_content_and_details():
    result = secret_entry_result("secret_entry.start", response())
    assert result.details["is_error"] is False
    assert "UNTRUSTED_FREE_TEXT" not in result.text
    assert result.details["structured_content"] == payload()
    assert "outside agent browser control" in result.text
    assert "secret_entry.status" in result.text


@pytest.mark.parametrize(
    "changes",
    [
        {"value": "SYNTHETIC_CREDENTIAL"},
        {"token": "SYNTHETIC_CREDENTIAL"},
        {"entry_url": "javascript:alert('SYNTHETIC_CREDENTIAL')"},
        {"entry_url": "https://user:SYNTHETIC_CREDENTIAL@ui.example.test/path"},
        {"requirement_reference": "unbound"},
        {"secret_uid": "0bf71e97-a624-4190-a5dd-5c435815d025"},
    ],
)
def test_malformed_or_credential_bearing_responses_fail_closed(changes):
    result = secret_entry_result("secret_entry.status", response(**changes))
    assert result.details["is_error"] is True
    assert "SYNTHETIC_CREDENTIAL" not in result.text + json.dumps(result.details)
    assert "structured_content" not in result.details


@pytest.mark.asyncio
async def test_idempotency_belongs_to_host_and_is_scoped_to_session_and_tool_call():
    backend = client()
    first = create_mainsequence_mcp_tools(backend, session_uid="session-one")[0]
    second = create_mainsequence_mcp_tools(backend, session_uid="session-two")[0]
    assert "idempotency_key" not in first.parameters["properties"]
    assert "idempotency_key" not in first.parameters["required"]
    for tool in (first, first, second):
        await tool.execute("same-call", {"name": "SERVICE_KEY", "purpose": "Connect integration"})
    keys = [call.args[1]["idempotency_key"] for call in backend.call_tool.await_args_list]
    assert UUID(keys[0])
    assert keys[0] == keys[1]
    assert keys[2] != keys[0]


@pytest.mark.asyncio
async def test_value_argument_is_not_sent_to_backend():
    backend = client()
    tool = create_mainsequence_mcp_tools(backend)[0]
    result = await tool.execute(
        "call", {"name": "SERVICE_KEY", "purpose": "Connect", "value": "SYNTHETIC_CREDENTIAL"}
    )
    backend.call_tool.assert_not_awaited()
    assert "SYNTHETIC_CREDENTIAL" not in result.text + json.dumps(result.details)


@pytest.mark.asyncio
async def test_transport_failure_does_not_echo_exception():
    backend = client()
    backend.call_tool.side_effect = RuntimeError("SYNTHETIC_CREDENTIAL")
    result = await create_mainsequence_mcp_tools(backend)[0].execute(
        "call", {"name": "SERVICE_KEY", "purpose": "Connect"}
    )
    assert result.details["is_error"] is True
    assert "SYNTHETIC_CREDENTIAL" not in result.text + json.dumps(result.details)


def test_a2a_waits_for_backend_requirement_and_other_conversations_are_unaffected():
    task = TaskExecutionContext(
        task_uid="task",
        task_id="public-task",
        context_id="context",
        attempt_uid="attempt",
        holder_id="holder",
        lease_token="private-proof",
    )
    with task_execution_scope(task):
        secret_entry_result("secret_entry.start", response())
    assert task.interruption_status == "auth_required"
    assert payload()["requirement_reference"] in json.dumps(task.interruption_message)
    assert "private-proof" not in json.dumps(task.interruption_message)
    other = TaskExecutionContext(
        task_uid="other",
        task_id="other-task",
        context_id="other",
        attempt_uid="other",
        holder_id="other",
        lease_token="other-private",
    )
    with task_execution_scope(other):
        result = secret_entry_result("secret_entry.status", response())
    assert result.details["structured_content"]["status"] == "pending"
    assert other.interruption_status is None


@pytest.mark.asyncio
async def test_safe_request_state_is_persisted_for_reconnect_without_raw_mcp_text():
    record = AsyncMock()
    tool = create_mainsequence_mcp_tools(client(), session_uid="session", on_secret_entry=record)[0]
    await tool.execute("call", {"name": "SERVICE_KEY", "purpose": "Connect"})
    record.assert_awaited_once_with(payload())
    assert "UNTRUSTED_FREE_TEXT" not in json.dumps(record.await_args.args[0])


@pytest.mark.asyncio
async def test_reconnect_checks_django_and_never_repeats_a_start_or_value_write():
    from tau_agent.session import CustomEntry

    from ms_tau_sdk.runtime.secret_entry import refresh_secret_entries

    stored = CustomEntry(namespace="mainsequence.secret_entry", data=payload())
    backend = client()
    backend.call_tool.return_value = response(status="completed")
    record = AsyncMock()
    result = await refresh_secret_entries(
        entries=[stored],
        client=backend,
        private_proof={"lease_token": "PRIVATE_PROOF"},
        record=record,
    )
    backend.call_tool.assert_awaited_once_with(
        "secret_entry.status",
        {
            "request_uid": payload()["uid"],
            "organization_environment_uid": payload()["organization_environment_uid"],
        },
        meta={"mainsequence.ai/caller-session-proof/v1": {"lease_token": "PRIVATE_PROOF"}},
    )
    assert "Django confirmed storage" in result
    assert "PRIVATE_PROOF" not in result
    record.assert_awaited_once_with(payload(status="completed"))
    backend.call_tool.reset_mock()
    await refresh_secret_entries(
        entries=[
            stored,
            CustomEntry(namespace="mainsequence.secret_entry", data=payload(status="completed")),
        ],
        client=backend,
        private_proof=None,
        record=record,
    )
    backend.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_stays_pending_until_django_confirms_storage():
    from tau_agent.session import CustomEntry

    from ms_tau_sdk.runtime.secret_entry import refresh_secret_entries

    result = await refresh_secret_entries(
        entries=[CustomEntry(namespace="mainsequence.secret_entry", data=payload())],
        client=client(),
        private_proof=None,
        record=AsyncMock(),
    )
    assert '"status": "pending"' in result
    assert "Django confirmed storage" not in result
    assert "Continue dependent work only for confirmed completion" in result


@pytest.mark.asyncio
async def test_reconnect_fails_closed_on_transport_or_wrong_request_status():
    from tau_agent.session import CustomEntry

    from ms_tau_sdk.runtime.secret_entry import refresh_secret_entries

    for scenario in ("transport", "wrong_reference"):
        backend = client()
        if scenario == "transport":
            backend.call_tool.side_effect = RuntimeError("SYNTHETIC_CREDENTIAL")
        else:
            backend.call_tool.return_value = response(uid="83ec49cc-5199-48c0-acbe-078f5700ac3f")
        record = AsyncMock()
        with pytest.raises(RuntimeError, match="status could not be verified") as error:
            await refresh_secret_entries(
                entries=[CustomEntry(namespace="mainsequence.secret_entry", data=payload())],
                client=backend,
                private_proof=None,
                record=record,
            )
        assert "SYNTHETIC_CREDENTIAL" not in str(error.value)
        record.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_persisted_entry_cannot_silently_skip_verification():
    from tau_agent.session import CustomEntry

    from ms_tau_sdk.runtime.secret_entry import refresh_secret_entries

    backend = client()
    with pytest.raises(RuntimeError, match="Stored Secret entry status is invalid") as error:
        await refresh_secret_entries(
            entries=[
                CustomEntry(
                    namespace="mainsequence.secret_entry", data=payload(value="SYNTHETIC_PRIVATE")
                )
            ],
            client=backend,
            private_proof=None,
            record=AsyncMock(),
        )
    assert "SYNTHETIC_PRIVATE" not in str(error.value)
    backend.call_tool.assert_not_awaited()
