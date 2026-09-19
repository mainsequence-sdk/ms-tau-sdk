import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import StreamingResponse
from starlette.requests import Request

from ms_tau_sdk.api.a2a import (
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
from ms_tau_sdk.backend.models import (
    AgentCardEnvelope,
    AgentSession,
    AgentTask,
    AgentTaskCreateResult,
    AgentTaskDispatch,
    AgentTaskExecutionAttempt,
)
from ms_tau_sdk.runtime.events import TauRuntimeEvent
from ms_tau_sdk.settings import TauSDKSettings


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/a2a/rpc",
            "headers": [
                (b"x-caller-kind", b"user"),
                (b"x-user-uid", b"2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d"),
            ],
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
        TauSDKSettings(_env_file=None),
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
        harness_version="0.4.2",
    )
    client.create_task.return_value = AgentTaskCreateResult(
        task=AgentTask(
            uid="backend-task-1",
            task_id="task-1",
            context_id="session-1",
            agent_uid="agent-1",
            status="submitted",
        ),
        created=True,
    )
    manager = AsyncMock()
    config = TauSDKSettings(_env_file=None)

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
    claimed = AgentTaskExecutionAttempt(
        uid="attempt-1",
        dispatch_uid="dispatch-1",
        attempt_number=1,
        state="claimed",
    )
    client.list_task_dispatches.return_value = [
        AgentTaskDispatch(uid="dispatch-1", state="pending")
    ]
    client.claim_task_dispatch.return_value = claimed
    client.start_task_attempt.return_value = claimed.model_copy(update={"state": "running"})
    client.create_task_output.return_value = {"uid": "output-1", "revision": 1}
    client.finalize_task_output.return_value = {"uid": "output-1", "revision": 2}

    async def get_task(_task_uid):
        return completed if client.settle_task_attempt.await_count else working

    client.get_task.side_effect = get_task
    client.settle_task_attempt.return_value = claimed.model_copy(update={"state": "completed"})

    class Manager:
        draining = False
        settings = TauSDKSettings(_env_file=None)

        async def task_execution_fence(self, _context_id):
            return SimpleNamespace(holder_id="holder-1", lease_token="lease-1")

        async def prompt(self, _context_id, _prompt, *, provenance=None):
            yield TauRuntimeEvent(
                type="text_delta",
                data={"contentIndex": 0, "delta": "hel"},
            )
            yield TauRuntimeEvent(
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
            provenance={"channel": "a2a", "origin": "agent", "actorKind": "agent"},
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
    client.start_task_attempt.assert_awaited_once()
    client.settle_task_attempt.assert_awaited_once_with(
        task.uid,
        attempt_uid="attempt-1",
        holder_id="holder-1",
        lease_token="lease-1",
        status="completed",
    )
