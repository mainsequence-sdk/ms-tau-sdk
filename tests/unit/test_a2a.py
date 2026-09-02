import base64
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from tau_agent.events import MessageEndEvent, MessageUpdateEvent
from tau_agent.messages import AssistantMessage, StopReason
from tau_agent.provider_events import TextDeltaEvent
from tau_coding.events import SessionAgentEndEvent

from astro.api.a2a import (
    RESPONSE_KIND_EXTENSION_URI,
    REST_BASE,
    _agent_message,
    _collect_turn,
    _materialize_pdfs,
    _output_contract,
    router,
)
from astro.api.dependencies import backend, runtime_manager, settings
from astro.backend.models import (
    AgentCardEnvelope,
    AgentSession,
    AgentTask,
    AgentTaskCreateResult,
)
from astro.runtime.events import translate_tau_event
from astro.settings import Settings


def _assistant_message(
    text: str = "",
    *,
    stop_reason: StopReason = "stop",
    error_message: str | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        content=text,
        api="responses",
        provider="openai-codex",
        model="gpt-5.3-codex-spark",
        stop_reason=stop_reason,
        error_message=error_message,
    )


class _TauEventManager:
    def __init__(self, *events: object) -> None:
        self.events = events
        self.delivered_sessions: list[str] = []
        self.provenances: list[object] = []

    async def prompt(self, _context_id: str, _prompt: str, *, provenance=None):
        self.provenances.append(provenance)
        for event in self.events:
            yield translate_tau_event(event)

    async def cancel(self, _context_id: str) -> bool:
        return True

    def mark_response_delivered(self, session_uid: str) -> bool:
        self.delivered_sessions.append(session_uid)
        return True


def _direct_message_client() -> tuple[AsyncMock, AgentTask]:
    task = AgentTask(
        uid="backend-task-1",
        task_id="task-1",
        context_id="session-1",
        agent_uid="agent-1",
        status="submitted",
    )
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
        agent_card=None,
    )
    client.create_task.return_value = AgentTaskCreateResult(task=task, created=True)
    client.update_task_status.side_effect = [
        task.model_copy(update={"status": "working"}),
        task.model_copy(update={"status": "completed"}),
    ]
    return client, task


async def _direct_message_response(
    manager: _TauEventManager,
    *,
    explicit_response_kind: bool = True,
) -> tuple[httpx.Response, AsyncMock]:
    client, _task = _direct_message_client()
    config = Settings(_env_file=None)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: config
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        body = {
            "message": {
                "messageId": "message-1",
                "role": "ROLE_USER",
                "contextId": "session-1",
                "parts": [{"text": "What do the two DataNodes do?"}],
            },
            "configuration": {
                "acceptedOutputModes": ["text/plain"],
            },
        }
        headers = {}
        if explicit_response_kind:
            body["configuration"]["responseKind"] = "message"
            headers["A2A-Extensions"] = RESPONSE_KIND_EXTENSION_URI
        response = await http.post(
            f"{REST_BASE}/message:send",
            json=body,
            headers=headers,
        )
    return response, client


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
    assert message["parts"] == [{"data": {"ok": True}, "mediaType": "application/json"}]


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
async def test_collect_turn_prefers_final_tau_message_over_whitespace_delta():
    partial = _assistant_message(" ")
    final = _assistant_message("The first DataNode loads data; the second transforms it.")
    manager = _TauEventManager(
        MessageUpdateEvent(
            message=partial,
            assistant_message_event=TextDeltaEvent(
                content_index=0,
                delta=" ",
                partial=partial,
            ),
        ),
        MessageEndEvent(message=final),
        SessionAgentEndEvent(messages=[final], will_retry=False),
    )

    result = await _collect_turn(
        manager,  # type: ignore[arg-type]
        "session-1",
        "What do the two DataNodes do?",
        max_output_bytes=1024,
    )

    assert result == "The first DataNode loads data; the second transforms it."


@pytest.mark.asyncio
async def test_collect_turn_uses_final_tau_assistant_after_tool_turn():
    tool_turn = _assistant_message("I will inspect the project.", stop_reason="toolUse")
    final = _assistant_message("The two DataNodes load and transform the tutorial data.")
    manager = _TauEventManager(
        MessageUpdateEvent(
            message=tool_turn,
            assistant_message_event=TextDeltaEvent(
                content_index=0,
                delta="I will inspect the project.",
                partial=tool_turn,
            ),
        ),
        MessageEndEvent(message=tool_turn),
        MessageEndEvent(message=final),
        SessionAgentEndEvent(messages=[tool_turn, final], will_retry=False),
    )

    result = await _collect_turn(
        manager,  # type: ignore[arg-type]
        "session-1",
        "What do the two DataNodes do?",
        max_output_bytes=1024,
    )

    assert result == "The two DataNodes load and transform the tutorial data."


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_reason", ["error", "aborted"])
async def test_collect_turn_rejects_tau_terminal_failure(stop_reason: StopReason):
    failed = _assistant_message(
        stop_reason=stop_reason,
        error_message="Provider could not complete the turn",
    )
    manager = _TauEventManager(
        MessageEndEvent(message=failed),
        SessionAgentEndEvent(messages=[failed], will_retry=False),
    )

    with pytest.raises(HTTPException, match="Provider could not complete the turn") as raised:
        await _collect_turn(
            manager,  # type: ignore[arg-type]
            "session-1",
            "What do the two DataNodes do?",
            max_output_bytes=1024,
        )

    assert raised.value.status_code == 502


@pytest.mark.asyncio
async def test_collect_turn_rejects_settled_tau_turn_without_text():
    final = _assistant_message()
    manager = _TauEventManager(
        MessageEndEvent(message=final),
        SessionAgentEndEvent(messages=[final], will_retry=False),
    )

    with pytest.raises(HTTPException, match="no textual answer") as raised:
        await _collect_turn(
            manager,  # type: ignore[arg-type]
            "session-1",
            "What do the two DataNodes do?",
            max_output_bytes=1024,
        )

    assert raised.value.status_code == 502


@pytest.mark.asyncio
async def test_direct_message_send_returns_final_tau_answer():
    partial = _assistant_message(" ")
    final = _assistant_message("The two DataNodes load and transform the tutorial data.")
    response, client = await _direct_message_response(
        _TauEventManager(
            MessageUpdateEvent(
                message=partial,
                assistant_message_event=TextDeltaEvent(
                    content_index=0,
                    delta=" ",
                    partial=partial,
                ),
            ),
            MessageEndEvent(message=final),
            SessionAgentEndEvent(messages=[final], will_retry=False),
        )
    )

    assert response.status_code == 200
    assert response.json()["message"]["parts"] == [
        {"text": "The two DataNodes load and transform the tutorial data."}
    ]
    client.create_task.assert_not_awaited()
    client.add_task_message.assert_not_awaited()
    client.update_task_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_omitted_response_kind_defaults_to_direct_message_without_task():
    final = _assistant_message("The direct answer.")
    response, client = await _direct_message_response(
        _TauEventManager(
            MessageEndEvent(message=final),
            SessionAgentEndEvent(messages=[final], will_retry=False),
        ),
        explicit_response_kind=False,
    )

    assert response.status_code == 200
    assert response.json()["message"]["kind"] == "message"
    client.get_agent_card.assert_not_awaited()
    client.get_session.assert_not_awaited()
    client.create_task.assert_not_awaited()
    client.add_task_message.assert_not_awaited()
    client.update_task_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_message_send_rejects_empty_tau_answer():
    final = _assistant_message()
    response, client = await _direct_message_response(
        _TauEventManager(
            MessageEndEvent(message=final),
            SessionAgentEndEvent(messages=[final], will_retry=False),
        )
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Agent turn produced no textual answer"
    client.create_task.assert_not_awaited()
    client.add_task_message.assert_not_awaited()
    client.update_task_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_message_send_surfaces_tau_terminal_failure():
    failed = _assistant_message(
        stop_reason="error",
        error_message="Provider could not complete the turn",
    )
    response, client = await _direct_message_response(
        _TauEventManager(
            MessageEndEvent(message=failed),
            SessionAgentEndEvent(messages=[failed], will_retry=False),
        )
    )

    assert response.status_code == 502
    assert response.json()["detail"] == ("Agent turn failed: Provider could not complete the turn")
    client.create_task.assert_not_awaited()
    client.add_task_message.assert_not_awaited()
    client.update_task_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_send_rejects_legacy_return_immediately():
    client, _task = _direct_message_client()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: _TauEventManager()
    app.dependency_overrides[settings] = lambda: Settings(_env_file=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            json={
                "message": {
                    "messageId": "message-1",
                    "role": "ROLE_USER",
                    "contextId": "session-1",
                    "parts": [{"text": "Do this."}],
                },
                "configuration": {"returnImmediately": False},
            },
        )

    assert response.status_code == 400
    assert "configuration.returnImmediately is not supported" in response.json()["detail"]
    client.get_agent_card.assert_not_awaited()
    client.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_send_task_requires_advertised_task_and_returns_task():
    client, task = _direct_message_client()
    client.get_agent_card.return_value = AgentCardEnvelope(
        agent_session_uid="session-1",
        agent_uid="agent-1",
        agent_card={
            "capabilities": {
                "extensions": [
                    {
                        "uri": RESPONSE_KIND_EXTENSION_URI,
                        "required": False,
                        "params": {
                            "supportedResponseKinds": ["message", "task"],
                            "defaultResponseKind": "message",
                        },
                    }
                ]
            }
        },
    )
    final = _assistant_message("Finished asynchronously.")

    class BackgroundManager(_TauEventManager):
        background = None

        def create_background_task(self, coroutine, *, name, operation_uid=None):
            del name, operation_uid
            self.background = coroutine

    manager = BackgroundManager(
        MessageEndEvent(message=final),
        SessionAgentEndEvent(messages=[final], will_retry=False),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: Settings(_env_file=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            headers={"A2A-Extensions": RESPONSE_KIND_EXTENSION_URI},
            json={
                "message": {
                    "messageId": "message-1",
                    "role": "ROLE_USER",
                    "contextId": "session-1",
                    "parts": [{"text": "Run this asynchronously."}],
                },
                "configuration": {"responseKind": "task"},
            },
        )

    assert response.status_code == 200
    assert response.json()["task"]["kind"] == "task"
    assert response.json()["task"]["id"] == task.task_id
    assert manager.background is not None
    await manager.background
    client.create_task.assert_awaited_once()
    client.add_task_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_message_send_rejects_task_when_agent_card_is_message_only():
    client, _task = _direct_message_client()
    manager = AsyncMock()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: Settings(_env_file=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            headers={"A2A-Extensions": RESPONSE_KIND_EXTENSION_URI},
            json={
                "message": {
                    "messageId": "message-1",
                    "role": "ROLE_USER",
                    "contextId": "session-1",
                    "parts": [{"text": "Run this asynchronously."}],
                },
                "configuration": {"responseKind": "task"},
            },
        )

    assert response.status_code == 400
    assert "not advertised" in response.json()["detail"]
    client.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_message_send_stamps_the_turn_as_agent_a2a_provenance():
    partial = _assistant_message(" ")
    final = _assistant_message("Done.")
    manager = _TauEventManager(
        MessageUpdateEvent(
            message=partial,
            assistant_message_event=TextDeltaEvent(
                content_index=0,
                delta=" ",
                partial=partial,
            ),
        ),
        MessageEndEvent(message=final),
        SessionAgentEndEvent(messages=[final], will_retry=False),
    )

    response, _client = await _direct_message_response(manager)

    assert response.status_code == 200
    assert manager.provenances == [{"channel": "a2a", "origin": "agent"}]
