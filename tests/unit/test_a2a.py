import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from tau_agent.events import MessageEndEvent, MessageUpdateEvent
from tau_agent.messages import AssistantMessage, StopReason
from tau_agent.provider_events import TextDeltaEvent
from tau_coding.events import SessionAgentEndEvent

from ms_tau_sdk.api.a2a import (
    RESPONSE_KIND_EXTENSION_URI,
    REST_BASE,
    _agent_message,
    _collect_turn,
    _materialize_pdfs,
    _output_contract,
    _request_parts,
    router,
)
from ms_tau_sdk.api.dependencies import backend, runtime_manager, settings
from ms_tau_sdk.backend.models import (
    AgentCardEnvelope,
    AgentSession,
    AgentTask,
    AgentTaskCreateResult,
    AgentTaskDispatch,
    AgentTaskExecutionAttempt,
    AgentTaskSnapshot,
)
from ms_tau_sdk.errors import BackendError
from ms_tau_sdk.runtime.events import translate_tau_event
from ms_tau_sdk.settings import TauSDKSettings


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


USER_CALLER_HEADERS = {
    "X-Caller-Kind": "user",
    "X-User-UID": "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d",
    "X-Username": "jose",
}

AGENT_CALLER_HEADERS = {
    "X-Caller-Kind": "agent",
    "X-User-UID": "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d",
    "X-Username": "jose",
    "X-Caller-Agent-UID": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "X-Caller-Coding-Agent-Service-UID": "f0e1d2c3-b4a5-4968-8776-655443322110",
    "X-Caller-Agent-Session-UID": "11111111-2222-4333-8444-555555555555",
}


class _TauEventManager:
    draining = False

    def __init__(self, *events: object) -> None:
        self.events = events
        self.delivered_sessions: list[str] = []
        self.provenances: list[object] = []

    async def prompt(self, _context_id: str, _prompt: str, *, provenance=None, turn_uid=None):
        self.provenances.append(provenance)
        for event in self.events:
            yield translate_tau_event(event)

    async def cancel(self, _context_id: str) -> bool:
        return True

    async def task_execution_fence(self, _context_id: str):
        return SimpleNamespace(holder_id="holder-1", lease_token="lease-1")

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
        harness_version="0.4.2",
    )
    client.get_agent_card.return_value = AgentCardEnvelope(
        agent_session_uid="session-1",
        agent_uid="agent-1",
        agent_card=None,
    )
    client.create_task.return_value = AgentTaskCreateResult(task=task, created=True)
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
    client.append_task_output.return_value = {"uid": "output-1", "revision": 2}
    client.finalize_task_output.return_value = {"uid": "output-1", "revision": 2}
    client.list_task_messages.return_value = []
    working = task.model_copy(update={"status": "working"})
    completed = task.model_copy(update={"status": "completed"})

    async def get_task(_task_uid, **_kwargs):
        return completed if client.settle_task_attempt.await_count else working

    client.get_task.side_effect = get_task
    client.settle_task_attempt.return_value = claimed.model_copy(update={"state": "completed"})
    return client, task


async def _direct_message_response(
    manager: _TauEventManager,
    *,
    explicit_response_kind: bool = True,
) -> tuple[httpx.Response, AsyncMock]:
    client, _task = _direct_message_client()
    config = TauSDKSettings(_env_file=None)
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
        headers = dict(USER_CALLER_HEADERS)
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
    config = TauSDKSettings(
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
    config = TauSDKSettings(_env_file=None, a2a_asset_root=tmp_path)
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


@pytest.mark.parametrize(
    ("message_update", "expected_detail"),
    [
        (
            {"role": "user"},
            "A2A v1 Message role must be ROLE_USER or ROLE_AGENT",
        ),
        (
            {"role": "ROLE_RESPONDER"},
            "A2A v1 Message role must be ROLE_USER or ROLE_AGENT",
        ),
        ({"kind": "message"}, "message.kind is not part of the A2A v1 Message envelope"),
        (
            {"parts": [{"kind": "text", "text": "Old part."}]},
            "message.parts[0].kind is not part of the A2A v1 Part envelope",
        ),
    ],
)
def test_request_parts_rejects_obsolete_v03_envelope(
    message_update: dict,
    expected_detail: str,
):
    body = {
        "message": {
            "messageId": "message-1",
            "role": "ROLE_USER",
            "contextId": "session-1",
            "parts": [{"text": "Current part."}],
            **message_update,
        }
    }

    with pytest.raises(HTTPException) as error:
        _request_parts(body, TauSDKSettings(_env_file=None))
    assert error.value.detail == expected_detail


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
        provenance={"channel": "a2a", "origin": "agent"},
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
        provenance={"channel": "a2a", "origin": "agent"},
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
            provenance={"channel": "a2a", "origin": "agent"},
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
            provenance={"channel": "a2a", "origin": "agent"},
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
    client.settle_task_attempt.assert_not_awaited()


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
    assert response.json()["message"]["role"] == "ROLE_AGENT"
    assert "kind" not in response.json()["message"]
    client.get_agent_card.assert_not_awaited()
    client.get_session.assert_not_awaited()
    client.create_task.assert_not_awaited()
    client.settle_task_attempt.assert_not_awaited()


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
    client.settle_task_attempt.assert_not_awaited()


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
    client.settle_task_attempt.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_send_accepts_standard_return_immediately_for_message():
    client, _task = _direct_message_client()
    final = _assistant_message("Direct answer.")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: _TauEventManager(
        MessageEndEvent(message=final),
        SessionAgentEndEvent(messages=[final], will_retry=False),
    )
    app.dependency_overrides[settings] = lambda: TauSDKSettings(_env_file=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            headers=USER_CALLER_HEADERS,
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

    assert response.status_code == 200
    assert response.json()["message"]["parts"] == [{"text": "Direct answer."}]
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

        def create_a2a_task_execution(self, task_uid, coroutine, *, name):
            del task_uid, name
            self.background = coroutine
            return None, True

    manager = BackgroundManager(
        MessageEndEvent(message=final),
        SessionAgentEndEvent(messages=[final], will_retry=False),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: TauSDKSettings(_env_file=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            headers={"A2A-Extensions": RESPONSE_KIND_EXTENSION_URI, **USER_CALLER_HEADERS},
            json={
                "message": {
                    "messageId": "message-1",
                    "role": "ROLE_USER",
                    "contextId": "session-1",
                    "parts": [{"text": "Run this asynchronously."}],
                },
                "configuration": {
                    "responseKind": "task",
                    "returnImmediately": True,
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"
    assert "kind" not in response.json()["task"]
    assert response.json()["task"]["id"] == task.task_id
    assert manager.background is not None
    await manager.background
    client.create_task.assert_awaited_once()
    client.claim_task_dispatch.assert_awaited_once_with(
        task.uid,
        holder_id="holder-1",
        lease_token="lease-1",
        dispatch_uid="dispatch-1",
        executor_instance_id="holder-1",
    )
    client.start_task_attempt.assert_awaited_once()
    client.create_task_output.assert_awaited_once()
    client.finalize_task_output.assert_awaited_once()
    client.settle_task_attempt.assert_awaited_once()


@pytest.mark.asyncio
async def test_immediate_task_return_survives_local_accelerator_claim_failure():
    client, task = _direct_message_client()
    client.claim_task_dispatch.side_effect = BackendError("claim unavailable")
    client.get_agent_card.return_value = AgentCardEnvelope(
        agent_session_uid="session-1",
        agent_uid="agent-1",
        agent_card={
            "capabilities": {
                "extensions": [
                    {
                        "uri": RESPONSE_KIND_EXTENSION_URI,
                        "params": {"supportedResponseKinds": ["message", "task"]},
                    }
                ]
            }
        },
    )

    class Manager(_TauEventManager):
        background = None

        def create_a2a_task_execution(self, task_uid, coroutine, *, name):
            del task_uid, name
            self.background = coroutine
            return None, True

    manager = Manager()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: TauSDKSettings(_env_file=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            headers={"A2A-Extensions": RESPONSE_KIND_EXTENSION_URI, **USER_CALLER_HEADERS},
            json={
                "message": {
                    "messageId": "message-1",
                    "role": "ROLE_USER",
                    "contextId": "session-1",
                    "parts": [{"text": "Run durably."}],
                },
                "configuration": {
                    "responseKind": "task",
                    "returnImmediately": True,
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["task"]["id"] == task.task_id
    assert response.json()["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"
    assert manager.background is None
    client.create_task.assert_awaited_once()
    assert client.create_task.await_args.args[0]["initial_message"]["extensions"] == []


@pytest.mark.asyncio
async def test_message_send_task_waits_when_return_immediately_is_false():
    client, task = _direct_message_client()
    client.get_agent_card.return_value = AgentCardEnvelope(
        agent_session_uid="session-1",
        agent_uid="agent-1",
        agent_card={
            "capabilities": {
                "extensions": [
                    {
                        "uri": RESPONSE_KIND_EXTENSION_URI,
                        "params": {"supportedResponseKinds": ["message", "task"]},
                    }
                ]
            }
        },
    )
    working = task.model_copy(update={"status": "working"})
    completed = task.model_copy(update={"status": "completed"})
    settled = False

    async def get_task(_task_uid, **_kwargs):
        return completed if settled else working

    async def settle_task_attempt(*args, **kwargs):
        nonlocal settled
        settled = True
        return completed

    async def get_task_snapshot(_task_uid):
        current = completed if settled else working
        return AgentTaskSnapshot(task=current, event_cursor=1 if settled else 0)

    client.get_task.side_effect = get_task
    client.get_task_snapshot.side_effect = get_task_snapshot
    client.settle_task_attempt.side_effect = settle_task_attempt
    final = _assistant_message("Finished before returning.")

    class RunningManager(_TauEventManager):
        background = None

        def create_a2a_task_execution(self, task_uid, coroutine, *, name):
            del task_uid
            self.background = asyncio.create_task(coroutine, name=name)
            return self.background, True

    manager = RunningManager(
        MessageEndEvent(message=final),
        SessionAgentEndEvent(messages=[final], will_retry=False),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: TauSDKSettings(
        _env_file=None,
        a2a_task_event_poll_interval_seconds=0.05,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            headers={"A2A-Extensions": RESPONSE_KIND_EXTENSION_URI, **USER_CALLER_HEADERS},
            json={
                "message": {
                    "messageId": "message-1",
                    "role": "ROLE_USER",
                    "contextId": "session-1",
                    "parts": [{"text": "Run and wait."}],
                },
                "configuration": {
                    "responseKind": "task",
                    "returnImmediately": False,
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert manager.background is not None
    await manager.background
    client.claim_task_dispatch.assert_awaited_once()
    client.settle_task_attempt.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_continuation_uses_authorized_backend_task_and_new_dispatch():
    client, task = _direct_message_client()
    interrupted = task.model_copy(update={"status": "input_required"})
    submitted = task.model_copy(update={"status": "submitted"})
    client.get_task_by_protocol_id.return_value = interrupted
    client.continue_task.return_value = submitted
    client.get_agent_card.return_value = AgentCardEnvelope(
        agent_session_uid="session-1",
        agent_uid="agent-1",
        agent_card={
            "capabilities": {
                "extensions": [
                    {
                        "uri": RESPONSE_KIND_EXTENSION_URI,
                        "params": {"supportedResponseKinds": ["message", "task"]},
                    }
                ]
            }
        },
    )

    class BackgroundManager(_TauEventManager):
        background = None

        def create_a2a_task_execution(self, task_uid, coroutine, *, name):
            del task_uid, name
            self.background = coroutine
            return None, True

    manager = BackgroundManager()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: TauSDKSettings(_env_file=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            headers={"A2A-Extensions": RESPONSE_KIND_EXTENSION_URI, **USER_CALLER_HEADERS},
            json={
                "message": {
                    "messageId": "message-2",
                    "taskId": "task-1",
                    "role": "ROLE_USER",
                    "contextId": "session-1",
                    "parts": [{"text": "Use account A."}],
                },
                "configuration": {
                    "responseKind": "task",
                    "returnImmediately": True,
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"
    client.create_task.assert_not_awaited()
    client.continue_task.assert_awaited_once_with(
        interrupted.uid,
        {
            "messageId": "message-2",
            "contextId": "session-1",
            "taskId": "task-1",
            "role": "ROLE_REQUESTER",
            "parts": [{"text": "Use account A."}],
            "metadata": {},
            "extensions": [],
            "referenceTaskIds": [],
        },
    )
    assert manager.background is not None
    manager.background.close()


@pytest.mark.asyncio
async def test_message_send_rejects_non_boolean_return_immediately():
    client, _task = _direct_message_client()
    manager = _TauEventManager()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: TauSDKSettings(_env_file=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            headers=USER_CALLER_HEADERS,
            json={
                "message": {
                    "messageId": "message-1",
                    "role": "ROLE_USER",
                    "contextId": "session-1",
                    "parts": [{"text": "Do this."}],
                },
                "configuration": {"returnImmediately": "true"},
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == ("configuration.returnImmediately must be a boolean")
    client.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_send_rejects_task_when_agent_card_is_message_only():
    client, _task = _direct_message_client()
    manager = AsyncMock()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: TauSDKSettings(_env_file=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            headers={"A2A-Extensions": RESPONSE_KIND_EXTENSION_URI, **USER_CALLER_HEADERS},
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
    assert manager.provenances == [
        {
            "channel": "a2a",
            "origin": "user",
            "actorKind": "user",
            "actorUid": USER_CALLER_HEADERS["X-User-UID"],
            "actorName": "jose",
        }
    ]


@pytest.mark.asyncio
async def test_direct_message_send_stamps_an_agent_caller_from_gateway_headers():
    final = _assistant_message("Done.")
    manager = _TauEventManager(
        MessageEndEvent(message=final),
        SessionAgentEndEvent(messages=[final], will_retry=False),
    )
    client, _task = _direct_message_client()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: TauSDKSettings(_env_file=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            headers={"A2A-Extensions": RESPONSE_KIND_EXTENSION_URI, **AGENT_CALLER_HEADERS},
            json={
                "message": {
                    "messageId": "message-1",
                    "role": "ROLE_USER",
                    "contextId": "session-1",
                    "parts": [{"text": "Do this."}],
                    # Identity in the body must never win over the headers.
                    "metadata": {"agentUid": "99999999-9999-4999-8999-999999999999"},
                },
                "configuration": {"responseKind": "message"},
            },
        )

    assert response.status_code == 200
    assert manager.provenances == [
        {
            "channel": "a2a",
            "origin": "agent",
            "actorKind": "agent",
            "actorUid": AGENT_CALLER_HEADERS["X-Caller-Agent-UID"],
            "callerAgentSessionUid": AGENT_CALLER_HEADERS["X-Caller-Agent-Session-UID"],
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"X-Caller-Kind": ""},
        {"X-Caller-Kind": "system"},
        {"X-Caller-Kind": "user", "X-User-UID": "not-a-uuid"},
        {"X-Caller-Kind": "user", "X-Caller-Agent-UID": AGENT_CALLER_HEADERS["X-Caller-Agent-UID"]},
        {"X-Caller-Kind": "agent"},
        {**AGENT_CALLER_HEADERS, "X-Caller-Agent-Session-UID": "ABC"},
    ],
    ids=[
        "absent",
        "unknown-kind",
        "user-bad-uid",
        "user-with-agent-header",
        "agent-missing",
        "agent-bad-session",
    ],
)
async def test_message_routes_reject_invalid_caller_identity_before_any_turn(headers):
    manager = _TauEventManager(_assistant_message("never"))
    client, _task = _direct_message_client()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: TauSDKSettings(_env_file=None)
    body = {
        "message": {
            "messageId": "message-1",
            "role": "ROLE_USER",
            "contextId": "session-1",
            "parts": [{"text": "Do this."}],
        },
        "configuration": {"responseKind": "message"},
    }
    expected = {
        "detail": "Missing or invalid caller identity headers.",
        "code": "runtime_caller_identity_invalid",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        send = await http.post(
            f"{REST_BASE}/message:send",
            headers={"A2A-Extensions": RESPONSE_KIND_EXTENSION_URI, **headers},
            json=body,
        )
        stream = await http.post(f"{REST_BASE}/message:stream", headers=headers, json=body)
        rpc = await http.post(
            "/api/a2a/rpc",
            headers=headers,
            json={"jsonrpc": "2.0", "id": "rpc-1", "method": "message/send", "params": body},
        )

    assert (send.status_code, send.json()) == (403, expected)
    assert (stream.status_code, stream.json()) == (403, expected)
    assert rpc.status_code == 200
    assert rpc.json()["error"]["message"] == expected["detail"]
    assert rpc.json()["error"]["data"] == {"code": expected["code"]}
    assert manager.provenances == []
    client.create_task.assert_not_awaited()
