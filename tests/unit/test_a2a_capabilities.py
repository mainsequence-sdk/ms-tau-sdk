import json
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import StreamingResponse
from starlette.requests import Request

from astro.api.a2a import (
    RESPONSE_KIND_EXTENSION_URI,
    _output_contract,
    _stream_task_events,
    delete_push_config,
    extended_agent_card,
    get_push_config,
    json_rpc,
    list_push_configs,
    set_push_config,
)
from astro.backend.models import AgentCardEnvelope, AgentSession, AgentTask
from astro.runtime.events import AstroRuntimeEvent
from astro.settings import Settings


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/a2a/rpc",
            "headers": [],
            "query_string": b"",
        }
    )


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
        _request(),
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
            "extensions": [
                {
                    "uri": RESPONSE_KIND_EXTENSION_URI,
                    "description": (
                        "Select whether message:send returns a completed message or an "
                        "asynchronous task."
                    ),
                    "required": False,
                    "params": {
                        "supportedResponseKinds": ["message"],
                        "defaultResponseKind": "message",
                    },
                }
            ],
        },
    }
    client.get_agent_card.assert_awaited_once_with("session-1")


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
        _request(),
    )

    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"
    client.create_task.assert_awaited_once()


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
