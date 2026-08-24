from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from astro.api.a2a import RESPONSE_KIND_EXTENSION_URI, REST_BASE, router
from astro.api.dependencies import backend, runtime_manager, settings
from astro.backend.models import (
    AgentCardEnvelope,
    AgentSession,
    AgentTask,
    AgentTaskCreateResult,
)
from astro.settings import Settings


class _BackgroundManager:
    def __init__(self) -> None:
        self.background = None

    def create_background_task(self, coroutine, *, name, operation_uid=None):
        del name, operation_uid
        self.background = coroutine


def _replayed_task(status: str) -> AgentTask:
    return AgentTask(
        uid="backend-task-1",
        task_id="task-1",
        context_id="session-1",
        agent_uid="agent-1",
        status=status,
        status_timestamp=datetime(2026, 8, 24, tzinfo=UTC),
        latest_message={
            "message_id": "answer-1",
            "role": "agent",
            "parts": [{"text": "Stored answer."}],
        },
    )


def _app(client: AsyncMock, manager: _BackgroundManager) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: Settings(_env_file=None)
    return app


def _client(task: AgentTask) -> AsyncMock:
    client = AsyncMock()
    client.get_session.return_value = AgentSession(
        uid="session-1",
        agent_uid="agent-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.3.1",
    )
    client.get_agent_card.return_value = AgentCardEnvelope(
        agent_session_uid="session-1",
        agent_uid="agent-1",
        agent_card={
            "capabilities": {
                "extensions": [
                    {
                        "uri": RESPONSE_KIND_EXTENSION_URI,
                        "params": {"supportedResponseKinds": ["task"]},
                    }
                ]
            }
        },
    )
    client.create_task.return_value = AgentTaskCreateResult(
        task=task,
        created=False,
    )
    return client


def _body() -> dict:
    return {
        "taskId": "task-1",
        "message": {
            "messageId": "message-1",
            "role": "ROLE_USER",
            "contextId": "session-1",
            "parts": [{"text": "Run this once."}],
        },
        "configuration": {"responseKind": "task"},
    }


@pytest.mark.parametrize("status", ["completed", "failed", "working"])
async def test_message_send_returns_replayed_task_without_scheduling_execution(
    asgi_client,
    status: str,
):
    task = _replayed_task(status)
    client = _client(task)
    manager = _BackgroundManager()

    async with asgi_client(_app(client, manager)) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            headers={"A2A-Extensions": RESPONSE_KIND_EXTENSION_URI},
            json=_body(),
        )

    assert response.status_code == 200
    assert response.json()["task"]["status"]["state"] == f"TASK_STATE_{status.upper()}"
    assert response.json()["task"]["artifacts"][0]["parts"] == [
        {"text": "Stored answer."}
    ]
    assert manager.background is None
    client.update_task_status.assert_not_awaited()


async def test_message_stream_returns_replayed_terminal_task_without_execution(
    asgi_client,
):
    task = _replayed_task("completed")
    client = _client(task)
    manager = _BackgroundManager()
    body = _body()
    body["configuration"] = {}

    async with asgi_client(_app(client, manager)) as http:
        response = await http.post(f"{REST_BASE}/message:stream", json=body)

    assert response.status_code == 200
    assert '"state":"TASK_STATE_COMPLETED"' in response.text
    assert '"final":true' in response.text
    assert manager.background is None
    client.update_task_status.assert_not_awaited()
