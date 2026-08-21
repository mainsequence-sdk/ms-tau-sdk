import base64
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from astro.api.a2a import (
    REST_BASE,
    _agent_message,
    _materialize_pdfs,
    _output_contract,
    _stream_task_events,
    delete_push_config,
    extended_agent_card,
    get_push_config,
    json_rpc,
    list_push_configs,
    router,
    set_push_config,
)
from astro.backend.models import AgentCardEnvelope, AgentSession, AgentTask
from astro.runtime.events import AstroRuntimeEvent
from astro.settings import Settings


def test_standard_a2a_routes_exclude_runtime_attach():
    paths = {route.path for route in router.routes}

    assert f"{REST_BASE}/message:send" in paths
    assert f"{REST_BASE}/message:stream" in paths
    assert f"{REST_BASE}/tasks/{{task_id}}:cancel" in paths
    assert "/api/a2a/rpc" in paths
    assert not any("/runtime" in path for path in paths)


def test_inline_pdf_is_validated_and_materialized_private(tmp_path):
    payload = b"%PDF-1.4\n% fixture\n"
    config = Settings(
        _env_file=None,
        a2a_asset_root=tmp_path,
        a2a_max_inline_file_bytes=1024,
    )

    paths = _materialize_pdfs(
        parts=[
            {
                "raw": base64.b64encode(payload).decode(),
                "filename": "report.pdf",
                "mediaType": "application/pdf",
            }
        ],
        context_id="session-1",
        message_id="message-1",
        config=config,
    )

    assert len(paths) == 1
    assert paths[0].read_bytes() == payload
    assert paths[0].stat().st_mode & 0o777 == 0o600


def test_inline_pdf_rejects_url_and_path_traversal(tmp_path):
    config = Settings(_env_file=None, a2a_asset_root=tmp_path)
    with pytest.raises(HTTPException, match="url is not supported"):
        _materialize_pdfs(
            parts=[
                {
                    "url": "https://example.test/report.pdf",
                    "filename": "report.pdf",
                    "mediaType": "application/pdf",
                }
            ],
            context_id="session-1",
            message_id="message-1",
            config=config,
        )
    with pytest.raises(HTTPException, match="safe PDF filename"):
        _materialize_pdfs(
            parts=[
                {
                    "raw": base64.b64encode(b"%PDF-1.4\n").decode(),
                    "filename": "../report.pdf",
                    "mediaType": "application/pdf",
                }
            ],
            context_id="session-1",
            message_id="message-1",
            config=config,
        )


def test_strict_json_message_uses_data_part():
    message = _agent_message(
        context_id="session-1",
        text='{"ok":true}',
        strict_json=True,
    )

    assert message["role"] == "ROLE_AGENT"
    assert message["parts"] == [
        {"data": {"ok": True}, "mediaType": "application/json"}
    ]


def test_output_contract_reads_schema_and_bounds_repair_attempts():
    contract = _output_contract(
        {
            "configuration": {"acceptedOutputModes": ["application/json"]},
            "metadata": {
                "https://mainsequence.ai/a2a/extensions/output-contract/v1": {
                    "strict": True,
                    "responseFormat": {
                        "type": "json_schema",
                        "schema": {
                            "type": "object",
                            "required": ["ok"],
                            "properties": {"ok": {"type": "boolean"}},
                        },
                    },
                    "jsonRepair": {"attempts": 99},
                }
            },
        }
    )

    assert contract.mode == "json_schema"
    assert contract.schema is not None
    assert contract.repair_attempts == 10


@pytest.mark.asyncio
async def test_push_notification_rest_operations_are_explicitly_unsupported():
    responses = [
        await set_push_config("task-1"),
        await list_push_configs("task-1"),
        await get_push_config("task-1", "push-1"),
        await delete_push_config("task-1", "push-1"),
    ]

    for response in responses:
        payload = json.loads(response.body)
        assert response.status_code == 400
        assert response.media_type == "application/a2a+json"
        assert payload["error"]["status"] == "FAILED_PRECONDITION"
        assert payload["error"]["details"] == [
            {
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": "PUSH_NOTIFICATION_NOT_SUPPORTED",
                "domain": "a2a-protocol.org",
            }
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "CreateTaskPushNotificationConfig",
        "GetTaskPushNotificationConfig",
        "ListTaskPushNotificationConfigs",
        "DeleteTaskPushNotificationConfig",
        "tasks/pushNotificationConfig/set",
        "tasks/pushNotificationConfig/get",
        "tasks/pushNotificationConfig/list",
        "tasks/pushNotificationConfig/delete",
    ],
)
async def test_push_notification_json_rpc_operations_are_explicitly_unsupported(method):
    client = AsyncMock()
    result = await json_rpc(
        {"jsonrpc": "2.0", "id": "rpc-1", "method": method, "params": {}},
        client,
        AsyncMock(),
        Settings(_env_file=None),
    )

    assert result == {
        "jsonrpc": "2.0",
        "id": "rpc-1",
        "error": {
            "code": -32003,
            "message": "Push notifications are not supported",
            "data": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "PUSH_NOTIFICATION_NOT_SUPPORTED",
                    "domain": "a2a-protocol.org",
                }
            ],
        },
    }
    assert client.mock_calls == []


@pytest.mark.asyncio
async def test_extended_agent_card_is_loaded_from_backend():
    client = AsyncMock()
    client.get_agent_card.return_value = AgentCardEnvelope(
        agent_session_uid="session-1",
        agent_uid="agent-1",
        agent_card={
            "name": "Portfolio Reviewer",
            "capabilities": {
                "streaming": True,
                "pushNotifications": True,
                "push_notifications": True,
            },
        },
    )

    result = await extended_agent_card(
        client,
        agent_session_uid="session-1",
        session_uid=None,
        context_id=None,
    )

    assert result["agent_card"] == {
        "name": "Portfolio Reviewer",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
        },
    }
    client.get_agent_card.assert_awaited_once_with("session-1")


@pytest.mark.asyncio
async def test_json_rpc_message_stream_returns_sse_response():
    client = AsyncMock()
    client.get_session.return_value = AgentSession(
        uid="session-1",
        agent_uid="agent-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.3.1",
    )
    client.create_task.return_value = AgentTask(
        uid="backend-task-1",
        task_id="task-1",
        context_id="session-1",
        agent_uid="agent-1",
        status="submitted",
    )
    manager = AsyncMock()
    config = Settings(_env_file=None)

    response = await json_rpc(
        {
            "jsonrpc": "2.0",
            "id": "rpc-1",
            "method": "message/stream",
            "params": {
                "taskId": "task-1",
                "message": {
                    "contextId": "session-1",
                    "messageId": "message-1",
                    "parts": [{"text": "hello"}],
                },
            },
        },
        client,
        manager,
        config,
    )

    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"
    client.create_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_a2a_stream_emits_incremental_artifact_and_final_task():
    task = AgentTask(
        uid="backend-task-1",
        task_id="task-1",
        context_id="session-1",
        agent_uid="agent-1",
        status="submitted",
    )
    working = task.model_copy(update={"status": "working"})
    completed = task.model_copy(update={"status": "completed"})
    client = AsyncMock()
    client.update_task_status.side_effect = [working, completed]

    class Manager:
        async def prompt(self, _context_id, _prompt):
            yield AstroRuntimeEvent(
                type="text_delta",
                data={"contentIndex": 0, "delta": "hel"},
            )
            yield AstroRuntimeEvent(
                type="text_delta",
                data={"contentIndex": 0, "delta": "lo"},
            )

        async def cancel(self, _context_id):
            return True

    events = [
        event
        async for event in _stream_task_events(
            client,
            Manager(),  # type: ignore[arg-type]
            task,
            prompt="hello",
            output_contract=_output_contract({}),
            max_output_bytes=1024,
        )
    ]

    artifact_updates = [event["artifactUpdate"] for event in events if "artifactUpdate" in event]
    assert [update["artifact"]["parts"][0]["text"] for update in artifact_updates] == [
        "hel",
        "lo",
        "",
    ]
    assert events[-1]["final"] is True
    assert events[-1]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    client.add_task_message.assert_awaited_once()
