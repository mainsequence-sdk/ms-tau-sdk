from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi import FastAPI

from astro.api.a2a import RESPONSE_KIND_EXTENSION_URI, REST_BASE, router
from astro.api.dependencies import backend, runtime_manager, settings
from astro.backend.models import AgentTask
from astro.runtime.events import AstroRuntimeEvent
from astro.settings import Settings

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
    app.dependency_overrides[settings] = lambda: Settings(_env_file=None)
    return app


class _DirectManager:
    settings = Settings(_env_file=None)

    def __init__(self) -> None:
        self.delivered_sessions: list[str] = []

    async def prompt(self, _context_id: str, _prompt: str, *, provenance=None):
        yield AstroRuntimeEvent(
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
    manager = AsyncMock()

    async with asgi_client(_app(client, manager), headers=USER_CALLER_HEADERS) as http:
        listed = await http.get(f"{REST_BASE}/tasks", params={"contextId": "session-1"})
        fetched = await http.get(f"{REST_BASE}/tasks/task-1")
        cancelled = await http.post(f"{REST_BASE}/tasks/task-1:cancel")
        subscribed = await http.get(f"{REST_BASE}/tasks/task-1:subscribe")

    assert listed.json()["tasks"][0]["status"]["state"] == "TASK_STATE_SUBMITTED"
    assert fetched.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert cancelled.json()["task"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert subscribed.headers["content-type"].startswith("text/event-stream")
    assert '"state":"TASK_STATE_COMPLETED"' in subscribed.text
    client.list_tasks.assert_awaited_once_with(context_id="session-1")
    manager.cancel.assert_awaited_once_with("session-1")
    client.cancel_task.assert_awaited_once_with("backend-task-1")


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
    client.add_task_message.assert_not_awaited()
    client.update_task_status.assert_not_awaited()


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
