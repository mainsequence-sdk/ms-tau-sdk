from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi import FastAPI

from ms_tau_sdk.api.a2a import RESPONSE_KIND_EXTENSION_URI, REST_BASE, router
from ms_tau_sdk.api.dependencies import backend, runtime_manager, settings
from ms_tau_sdk.backend.models import (
    AgentTask,
    AgentTaskEvent,
    AgentTaskEventPage,
    AgentTaskSnapshot,
)
from ms_tau_sdk.runtime.events import TauRuntimeEvent
from ms_tau_sdk.settings import TauSDKSettings

USER_CALLER_HEADERS = {
    "X-Caller-Kind": "user",
    "X-User-UID": "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d",
    "X-Username": "jose",
}


def _task(status: str = "submitted") -> AgentTask:
    return AgentTask(
        uid="backend-task-1",
        task_id="task-1",
        context_id="session-1",
        agent_uid="agent-1",
        status=status,
        status_timestamp=datetime(2026, 8, 24, tzinfo=UTC),
    )


def _app(client: AsyncMock, manager: object) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: TauSDKSettings(_env_file=None)
    return app


class _DirectManager:
    settings = TauSDKSettings(_env_file=None)

    def __init__(self) -> None:
        self.delivered_sessions: list[str] = []

    async def prompt(self, _context_id: str, _prompt: str, *, provenance=None):
        yield TauRuntimeEvent(
            type="message_end",
            data={
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Direct RPC answer."}],
                    "stopReason": "stop",
                }
            },
        )

    async def cancel(self, _context_id: str) -> bool:
        return True

    def mark_response_delivered(self, session_uid: str) -> bool:
        self.delivered_sessions.append(session_uid)
        return True


async def test_rest_task_list_get_cancel_and_subscribe_contract(asgi_client):
    submitted = _task()
    completed = _task("completed")
    canceled = _task("canceled")
    client = AsyncMock()
    client.list_tasks.return_value = [submitted]
    client.get_task_by_protocol_id.return_value = completed
    client.cancel_task.return_value = canceled
    client.get_task_snapshot_by_protocol_id.return_value = AgentTaskSnapshot(
        task=submitted,
        event_cursor=0,
    )
    client.list_task_events.return_value = AgentTaskEventPage(
        events=[
            AgentTaskEvent(
                sequence=1,
                event_type="status_changed",
                status="completed",
            )
        ],
        next_cursor=1,
    )
    client.get_task_snapshot.return_value = AgentTaskSnapshot(
        task=completed,
        event_cursor=1,
    )
    manager = AsyncMock()

    async with asgi_client(_app(client, manager), headers=USER_CALLER_HEADERS) as http:
        listed = await http.get(f"{REST_BASE}/tasks", params={"contextId": "session-1"})
        fetched = await http.get(f"{REST_BASE}/tasks/task-1")
        cancelled = await http.post(f"{REST_BASE}/tasks/task-1:cancel")
        subscribed = await http.get(f"{REST_BASE}/tasks/task-1:subscribe")

    assert listed.json()["tasks"][0]["status"]["state"] == "TASK_STATE_SUBMITTED"
    assert fetched.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert fetched.json()["task"]["status"]["timestamp"] == "2026-08-24T00:00:00Z"
    assert cancelled.json()["task"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert subscribed.headers["content-type"].startswith("text/event-stream")
    assert '"eventCursor":0' in subscribed.text
    assert '"state":"TASK_STATE_COMPLETED"' in subscribed.text
    client.list_tasks.assert_awaited_once_with(
        context_id="session-1",
        history_length=100,
    )
    manager.cancel.assert_awaited_once_with("session-1")
    client.cancel_task.assert_awaited_once_with("backend-task-1")


async def test_task_history_length_is_bounded_and_uses_public_a2a_roles(asgi_client):
    requester = {
        "messageId": "message-1",
        "taskId": "task-1",
        "contextId": "session-1",
        "role": "ROLE_REQUESTER",
        "parts": [{"text": "Start."}],
        "extensions": [],
    }
    responder = {
        "messageId": "message-2",
        "taskId": "task-1",
        "contextId": "session-1",
        "role": "ROLE_RESPONDER",
        "parts": [{"text": "More input is required."}],
        "extensions": ["https://example.test/input/v1"],
        "metadata": {"https://example.test/input/v1": {"field": "account"}},
    }
    client = AsyncMock()
    client.list_tasks.return_value = [_task().model_copy(update={"history": []})]
    client.get_task_by_protocol_id.return_value = _task().model_copy(
        update={"history": [responder]}
    )

    async with asgi_client(_app(client, AsyncMock()), headers=USER_CALLER_HEADERS) as http:
        without_history = await http.get(
            f"{REST_BASE}/tasks",
            params={"historyLength": 0},
        )
        with_history = await http.get(
            f"{REST_BASE}/tasks/task-1",
            params={"historyLength": 1},
        )
        invalid = await http.post(
            "/api/a2a/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "get-invalid-history",
                "method": "tasks/get",
                "params": {"id": "task-1", "historyLength": True},
            },
        )

    assert "history" not in without_history.json()["tasks"][0]
    public_message = with_history.json()["task"]["history"][0]
    assert public_message == {**responder, "role": "ROLE_AGENT"}
    assert public_message["extensions"] == ["https://example.test/input/v1"]
    assert invalid.json()["error"] == {
        "code": -32602,
        "message": "historyLength must be a non-negative integer",
    }
    client.list_tasks.assert_awaited_once_with(context_id="", history_length=0)
    client.get_task_by_protocol_id.assert_awaited_once_with(
        "task-1",
        history_length=1,
    )

    projected = _task().model_copy(update={"history": [requester, responder]})
    client.list_tasks.return_value = [projected]
    async with asgi_client(_app(client, AsyncMock()), headers=USER_CALLER_HEADERS) as http:
        capped = await http.get(
            f"{REST_BASE}/tasks",
            params={"historyLength": 1000},
        )
    assert [message["role"] for message in capped.json()["tasks"][0]["history"]] == [
        "ROLE_USER",
        "ROLE_AGENT",
    ]
    assert client.list_tasks.await_args.kwargs["history_length"] == 100


async def test_terminal_task_subscription_is_unsupported_for_rest_and_json_rpc(
    asgi_client,
):
    completed = _task("completed")
    client = AsyncMock()
    client.get_task_snapshot_by_protocol_id.return_value = AgentTaskSnapshot(
        task=completed,
        event_cursor=7,
    )
    manager = AsyncMock()

    async with asgi_client(_app(client, manager), headers=USER_CALLER_HEADERS) as http:
        rest = await http.get(f"{REST_BASE}/tasks/task-1:subscribe")
        rpc = await http.post(
            "/api/a2a/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "subscribe-1",
                "method": "SubscribeToTask",
                "params": {"id": "task-1"},
            },
        )

    assert rest.status_code == 400
    assert rest.json()["error"]["details"][0]["reason"] == "UNSUPPORTED_OPERATION"
    assert rpc.status_code == 200
    assert rpc.json()["error"]["code"] == -32004
    assert "terminal Task" in rpc.json()["error"]["message"]


async def test_json_rpc_direct_message_does_not_create_a_task(asgi_client):
    client = AsyncMock()
    manager = _DirectManager()

    async with asgi_client(_app(client, manager), headers=USER_CALLER_HEADERS) as http:
        response = await http.post(
            "/api/a2a/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "rpc-1",
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": "message-1",
                        "contextId": "session-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "Answer directly."}],
                    }
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["message"]["parts"] == [{"text": "Direct RPC answer."}]
    client.get_session.assert_not_awaited()
    client.create_task.assert_not_awaited()
    client.settle_task_attempt.assert_not_awaited()


async def test_json_rpc_task_operations_use_the_canonical_rest_implementation(asgi_client):
    submitted = _task()
    canceled = _task("canceled")
    client = AsyncMock()
    client.list_tasks.return_value = [submitted]
    client.get_task_by_protocol_id.return_value = submitted
    client.cancel_task.return_value = canceled
    manager = AsyncMock()
    app = _app(client, manager)

    async with asgi_client(app, headers=USER_CALLER_HEADERS) as http:
        listed = await http.post(
            "/api/a2a/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "list-1",
                "method": "tasks/list",
                "params": {"contextId": "session-1"},
            },
        )
        fetched = await http.post(
            "/api/a2a/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "get-1",
                "method": "tasks/get",
                "params": {"id": "task-1"},
            },
        )
        cancelled = await http.post(
            "/api/a2a/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "cancel-1",
                "method": "tasks/cancel",
                "params": {"id": "task-1"},
            },
        )

    assert listed.json()["result"]["tasks"][0]["id"] == "task-1"
    assert fetched.json()["result"]["task"]["id"] == "task-1"
    assert cancelled.json()["result"]["task"]["status"]["state"] == "TASK_STATE_CANCELED"
    manager.cancel.assert_awaited_once_with("session-1")


async def test_response_kind_requires_extension_and_is_rejected_for_streaming(asgi_client):
    client = AsyncMock()
    manager = AsyncMock()

    body = {
        "message": {
            "messageId": "message-1",
            "contextId": "session-1",
            "role": "ROLE_USER",
            "parts": [{"text": "Hello"}],
        },
        "configuration": {"responseKind": "message"},
    }
    async with asgi_client(_app(client, manager), headers=USER_CALLER_HEADERS) as http:
        missing_extension = await http.post(f"{REST_BASE}/message:send", json=body)
        streaming = await http.post(
            f"{REST_BASE}/message:stream",
            headers={"A2A-Extensions": RESPONSE_KIND_EXTENSION_URI},
            json=body,
        )

    assert missing_extension.status_code == 400
    assert "requires A2A-Extensions" in missing_extension.json()["detail"]
    assert streaming.status_code == 400
    assert "not valid for message:stream" in streaming.json()["detail"]
    client.create_task.assert_not_awaited()
