"""The person a hosted turn serves, and the calls project code makes for the work.

A hosted chat or A2A Message turn presents its request's verified caller assertion when it marks
the turn active, and a turn the platform starts for a caller delivery names that delivery. Whatever
the turn presents, the platform's answer to the turn start names the person the turn serves, or
nobody: it is the only source. Extension tools read that person with ``current_requester()`` and
call the platform and its applications with ``platform_client()``, which carries the person's
delegation by default. The platform, its key set and the applications are fakes; nothing leaves the
process.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, AsyncMock, Mock

import httpx
import pytest
import respx
from tau_agent.messages import AssistantMessage, ToolCall
from tau_agent.provider_events import AssistantDoneEvent
from tau_ai.fake import FakeProvider

from ms_tau_sdk import create_app, current_requester, platform_client
from ms_tau_sdk.api.a2a import (
    RESPONSE_KIND_EXTENSION_URI,
    REST_BASE,
    _create_backend_task,
    _request_caller,
    _task_caller_assertion,
)
from ms_tau_sdk.api.dependencies import backend, runtime_manager, settings
from ms_tau_sdk.api.request_identity import _CALLER_ASSERTION_SCOPE_KEY
from ms_tau_sdk.application import ApplicationServices
from ms_tau_sdk.backend.assertions import ASSERTION_HEADER, CallerAssertion, VerifiedCaller
from ms_tau_sdk.backend.client import LeaseProof, MainSequenceClient
from ms_tau_sdk.backend.models import (
    AgentCardEnvelope,
    AgentSession,
    AgentTask,
    AgentTaskCreateResult,
    AgentTaskDispatch,
    AgentTaskExecutionAttempt,
    DirectoryUser,
    ProviderControl,
    ProviderCredential,
    RuntimeState,
    TauTurnCommit,
)
from ms_tau_sdk.errors import BackendConflictError, BackendError, TauSDKError
from ms_tau_sdk.protocols.a2a_roles import A2AMessageDirection
from ms_tau_sdk.providers.factory import ProviderRuntime
from ms_tau_sdk.runtime import requester as requester_module
from ms_tau_sdk.runtime.events import TauRuntimeEvent
from ms_tau_sdk.runtime.manager import RUNTIME_CAPABILITIES, SessionRuntimeManager
from ms_tau_sdk.runtime.requester import (
    CallerDelivery,
    Requester,
    RequesterBindingError,
    TurnRequesterBinding,
    bind_turn_requester,
    register_runtime_platform,
    release_runtime_platform,
    unbind_turn_requester,
)
from ms_tau_sdk.runtime.session import ActiveSessionRuntime
from ms_tau_sdk.runtime.task_context import (
    TaskExecutionContext,
    active_task_execution,
    task_execution_scope,
)
from ms_tau_sdk.sessions.storage import BackendSessionStorage
from ms_tau_sdk.settings import TauSDKSettings

# The person the `platform_keys` fixture signs caller assertions for unless told otherwise.
OWNER_UID = "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d"
TEAM_UID = "7e6d5c4b-3a29-4180-9f7e-6d5c4b3a2918"
OTHER_UID = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"
# The workload User of a calling Agent.
AGENT_USER_UID = "c0ffee00-1234-4abc-8def-0123456789ab"
SESSION_UID = "11111111-aaaa-4bbb-8ccc-222222222222"
RELEASE_UID = "44444444-5555-4666-8777-888888888888"
LEASE_TOKEN = "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a"
RUNTIME_ACCESS_TOKEN = "runtime-access-token-for-the-agent"
RAW_ASSERTION = "raw-caller-assertion-of-the-request"
USER_CALLER_HEADERS = {"X-Caller-Kind": "user"}
AGENT_CALLER_HEADERS = {
    "X-Caller-Kind": "agent",
    "X-Caller-Agent-UID": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "X-Caller-Coding-Agent-Service-UID": "f0e1d2c3-b4a5-4968-8776-655443322110",
}


def _jwt(claims: dict[str, Any]) -> str:
    def encode(value: dict[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{encode({'alg': 'none'})}.{encode(claims)}.signature"


def _release_token(*, lifetime_seconds: int = 3600, marker: str = "first") -> str:
    return _jwt({"exp": int(time.time()) + lifetime_seconds, "marker": marker})


def _assertion(
    *,
    uid: str = OWNER_UID,
    lifetime_seconds: int = 300,
    token: str = RAW_ASSERTION,
) -> CallerAssertion:
    return CallerAssertion(
        caller=VerifiedCaller(uid=uid, team_uids=(TEAM_UID,), is_organization_admin=False),
        expires_at=int(time.time()) + lifetime_seconds,
        token=token,
    )


def _managed_settings(tmp_path: Path) -> TauSDKSettings:
    return TauSDKSettings(
        _env_file=None,
        runtime_credential_id="credential-id",
        workspace=tmp_path,
        startup_dependencies_enabled=False,
    )


# --- Turn start and the turn's requester -------------------------------------------------------


class _ToolTurnSession:
    """A Tau session whose turn runs one tool before it settles."""

    is_running = False

    def __init__(self, tool: Callable[[], Any]) -> None:
        self.tool = tool
        self.custom_entries: list[tuple[str, dict[str, Any]]] = []

    async def append_custom_entry(self, namespace: str, data: dict[str, Any]) -> None:
        self.custom_entries.append((namespace, data))

    async def prompt(self, _content: str) -> AsyncIterator[dict[str, Any]]:
        await self.tool()
        yield {"type": "text_delta", "delta": "ok"}
        yield {"type": "agent_settled"}

    def cancel(self) -> None:
        return None


def _storage(*, answered_requester: str | None) -> SimpleNamespace:
    async def begin_turn(**request: Any) -> RuntimeState:
        return RuntimeState(
            harness="tau",
            harness_protocol="tau-session-v1",
            harness_version="0.4.2",
            runtime_activity="working",
            active_turn_uid=request["turn_uid"],
            activity_sequence=request["activity_sequence"],
            requester_user_uid=answered_requester,
        )

    async def commit_turn(**request: Any) -> TauTurnCommit:
        return TauTurnCommit(
            turn_uid=request["turn_uid"],
            next_sequence=0,
            committed_at=datetime.now(UTC),
        )

    return SimpleNamespace(
        lease_token=LEASE_TOKEN,
        begin_turn=AsyncMock(side_effect=begin_turn),
        commit_turn=AsyncMock(side_effect=commit_turn),
        entries_at_sequence=AsyncMock(return_value=[]),
        flush=AsyncMock(),
        invalidate_lease=Mock(),
    )


def _turn_manager(
    config: TauSDKSettings,
    tool: Callable[[], Any],
    *,
    answered_requester: str | None = OWNER_UID,
) -> tuple[SessionRuntimeManager, SimpleNamespace]:
    manager = SessionRuntimeManager(settings=config, backend=AsyncMock(), providers=Mock())
    storage = _storage(answered_requester=answered_requester)
    manager._runtimes[SESSION_UID] = ActiveSessionRuntime(
        session_uid=SESSION_UID,
        holder_id=manager.holder_id,
        coding_session=_ToolTurnSession(tool),  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        provider=object(),
        provider_name="openai",
        model="gpt-5.4",
    )
    return manager, storage


async def _run_turn(manager: SessionRuntimeManager, **options: Any) -> None:
    async for _event in manager.prompt(SESSION_UID, "Show my rows.", **options):
        pass


async def _close(manager: SessionRuntimeManager) -> None:
    manager._runtimes.clear()
    await manager.aclose()


@pytest.mark.parametrize(
    ("answered_requester", "expected"),
    [
        pytest.param(OWNER_UID, Requester(uid=OWNER_UID, team_uids=(TEAM_UID,)), id="the_caller"),
        pytest.param(None, None, id="nobody"),
        # The platform's answer is the only source: the turn serves whoever it names. The caller's
        # teams belong to the caller, so another person has none.
        pytest.param(OTHER_UID, Requester(uid=OTHER_UID), id="someone_else"),
    ],
)
async def test_a_hosted_chat_turn_presents_its_assertion_and_serves_the_person_named(
    platform_keys,
    tmp_path,
    answered_requester,
    expected,
):
    seen: list[Requester | None] = []

    async def tool() -> None:
        seen.append(current_requester())

    manager, storage = _turn_manager(
        platform_keys.hosted_settings(tmp_path),
        tool,
        answered_requester=answered_requester,
    )

    await _run_turn(manager, caller_assertion=_assertion())

    storage.begin_turn.assert_awaited_once_with(
        turn_uid=ANY,
        activity_sequence=1,
        caller_assertion=RAW_ASSERTION,
    )
    assert seen == [expected]
    assert current_requester() is None
    await _close(manager)


@pytest.mark.parametrize(
    ("answered_requester", "expected"),
    [
        pytest.param(None, None, id="nobody"),
        # Agent A delegated work for the person: the platform names that person for B's turn.
        pytest.param(OWNER_UID, Requester(uid=OWNER_UID), id="the_delegating_person"),
    ],
)
async def test_an_agent_callers_turn_serves_the_person_the_platform_names(
    platform_keys,
    tmp_path,
    answered_requester,
    expected,
):
    seen: list[Requester | None] = []

    async def tool() -> None:
        seen.append(current_requester())

    manager, storage = _turn_manager(
        platform_keys.hosted_settings(tmp_path),
        tool,
        answered_requester=answered_requester,
    )

    await _run_turn(manager, caller_assertion=_assertion(uid=AGENT_USER_UID))

    assert storage.begin_turn.await_args.kwargs["caller_assertion"] == RAW_ASSERTION
    assert seen == [expected]
    await _close(manager)


async def test_an_expired_assertion_is_not_presented(platform_keys, tmp_path):
    seen: list[Requester | None] = []

    async def tool() -> None:
        seen.append(current_requester())

    manager, storage = _turn_manager(
        platform_keys.hosted_settings(tmp_path), tool, answered_requester=None
    )

    await _run_turn(manager, caller_assertion=_assertion(lifetime_seconds=-1))

    assert "caller_assertion" not in storage.begin_turn.await_args.kwargs
    assert seen == [None]
    await _close(manager)


@pytest.mark.parametrize("mode", ["local", "managed_without_hosting"])
async def test_a_runtime_that_does_not_verify_its_callers_serves_nobody(
    tmp_path,
    mode,
):
    seen: list[Requester | None] = []

    async def tool() -> None:
        seen.append(current_requester())

    config = _managed_settings(tmp_path)
    if mode == "local":
        config = config.model_copy(update={"local_mode": True})
    # Even an answer that names someone serves nobody here.
    manager, storage = _turn_manager(config, tool, answered_requester=OWNER_UID)

    await _run_turn(manager, caller_assertion=_assertion())

    assert "caller_assertion" not in storage.begin_turn.await_args.kwargs
    assert seen == [None]
    await _close(manager)


async def test_session_storage_presents_an_assertion_only_with_the_turn_it_starts():
    platform = AsyncMock()
    platform.patch_runtime_activity.return_value = RuntimeState(
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.4.2",
        runtime_activity="working",
    )
    storage = BackendSessionStorage(
        backend=platform,
        session_uid=SESSION_UID,
        lease_token=LEASE_TOKEN,
        holder_id="holder-1",
        initial_entries=[],
        initial_next_sequence=0,
    )

    await storage.begin_turn(turn_uid="turn-1", activity_sequence=1, caller_assertion=RAW_ASSERTION)
    await storage.begin_turn(turn_uid="turn-2", activity_sequence=2)
    await storage.begin_turn(
        turn_uid="turn-3", activity_sequence=3, caller_delivery_uid="delivery-1"
    )

    presented, without, resumed = platform.patch_runtime_activity.await_args_list
    assert presented.kwargs == {"caller_assertion": RAW_ASSERTION}
    assert presented.args[1].active_turn_uid == "turn-1"
    assert without.kwargs == {}
    assert RAW_ASSERTION not in presented.args[1].model_dump_json()
    assert "caller_delivery_uid" not in presented.args[1].model_dump(exclude_none=True)
    assert resumed.kwargs == {}
    assert resumed.args[1].caller_delivery_uid == "delivery-1"


@pytest.mark.parametrize(
    ("answered_requester", "expected"),
    [
        pytest.param(OWNER_UID, Requester(uid=OWNER_UID), id="the_delegating_person"),
        pytest.param(None, None, id="nobody"),
    ],
)
async def test_a_caller_delivery_turn_names_its_delivery_and_serves_the_person_named(
    platform_keys,
    tmp_path,
    answered_requester,
    expected,
):
    seen: list[Requester | None] = []

    async def tool() -> None:
        seen.append(current_requester())

    manager, storage = _turn_manager(
        platform_keys.hosted_settings(tmp_path),
        tool,
        answered_requester=answered_requester,
    )

    await _run_turn(manager, caller_delivery=CallerDelivery(uid="delivery-1"))

    storage.begin_turn.assert_awaited_once_with(
        turn_uid=ANY,
        activity_sequence=1,
        caller_delivery_uid="delivery-1",
    )
    assert seen == [expected]
    assert current_requester() is None
    await _close(manager)


async def test_without_hosting_a_caller_delivery_turn_names_nothing(tmp_path):
    seen: list[Requester | None] = []

    async def tool() -> None:
        seen.append(current_requester())

    manager, storage = _turn_manager(_managed_settings(tmp_path), tool)

    await _run_turn(manager, caller_delivery=CallerDelivery(uid="delivery-1"))

    storage.begin_turn.assert_awaited_once_with(turn_uid=ANY, activity_sequence=1)
    assert seen == [None]
    await _close(manager)


def _task_context(caller: VerifiedCaller | None = None) -> TaskExecutionContext:
    return TaskExecutionContext(
        task_uid="backend-task-1",
        task_id="task-1",
        context_id=SESSION_UID,
        attempt_uid="attempt-1",
        holder_id="holder-1",
        lease_token=LEASE_TOKEN,
        caller=caller,
    )


THE_CALLER = VerifiedCaller(uid=OWNER_UID, team_uids=(TEAM_UID,), is_organization_admin=False)


@pytest.mark.parametrize(
    ("hosted", "caller", "expected"),
    [
        pytest.param(
            True, THE_CALLER, Requester(uid=OWNER_UID, team_uids=(TEAM_UID,)), id="run_by_them"
        ),
        # A dispatched attempt has no caller, so the person it serves has no known teams.
        pytest.param(True, None, Requester(uid=OWNER_UID), id="dispatched"),
        pytest.param(False, THE_CALLER, None, id="not_hosted"),
    ],
)
async def test_a_task_turn_never_presents_an_assertion_and_serves_the_person_named(
    platform_keys,
    tmp_path,
    hosted,
    caller,
    expected,
):
    seen: list[Requester | None] = []

    async def tool() -> None:
        seen.append(current_requester())

    config = platform_keys.hosted_settings(tmp_path) if hosted else _managed_settings(tmp_path)
    manager, storage = _turn_manager(config, tool, answered_requester=OWNER_UID)

    with task_execution_scope(_task_context(caller)):
        await _run_turn(manager, caller_assertion=_assertion(), turn_uid="turn-of-attempt-1")

    storage.begin_turn.assert_awaited_once_with(turn_uid="turn-of-attempt-1", activity_sequence=1)
    assert seen == [expected]
    await _close(manager)


@pytest.mark.parametrize(
    "caller",
    [
        pytest.param(
            VerifiedCaller(uid=AGENT_USER_UID, team_uids=(TEAM_UID,), is_organization_admin=False),
            id="run_by_the_job",
        ),
        pytest.param(None, id="dispatched"),
    ],
)
async def test_a_task_a_job_asked_for_serves_nobody(platform_keys, tmp_path, caller):
    seen: list[Requester | None] = []

    async def tool() -> None:
        seen.append(current_requester())

    # A Job's workload asked for the Task, so the platform names nobody for its attempts.
    manager, _storage = _turn_manager(
        platform_keys.hosted_settings(tmp_path), tool, answered_requester=None
    )

    with task_execution_scope(_task_context(caller)):
        await _run_turn(manager, caller_assertion=_assertion(), turn_uid="turn-of-attempt-1")

    assert seen == [None]
    await _close(manager)


@pytest.mark.parametrize(
    ("named", "caller", "expected"),
    [
        pytest.param(OWNER_UID, THE_CALLER, Requester(uid=OWNER_UID, team_uids=(TEAM_UID,))),
        pytest.param(OTHER_UID, THE_CALLER, Requester(uid=OTHER_UID)),
        pytest.param(
            OWNER_UID,
            VerifiedCaller(uid=AGENT_USER_UID, team_uids=(TEAM_UID,), is_organization_admin=False),
            Requester(uid=OWNER_UID),
        ),
        pytest.param(None, THE_CALLER, None),
        pytest.param(OWNER_UID, None, Requester(uid=OWNER_UID)),
    ],
)
def test_the_person_named_gets_the_callers_teams_only_when_the_caller_is_that_person(
    platform_keys,
    tmp_path,
    named,
    caller,
    expected,
):
    manager = SessionRuntimeManager(
        settings=platform_keys.hosted_settings(tmp_path),
        backend=AsyncMock(),
        providers=Mock(),
    )
    runtime = SimpleNamespace(requester_user_uid=named)

    assert manager._turn_requester(runtime, caller=caller) == expected  # type: ignore[arg-type]


async def test_the_turns_delegation_ends_with_the_turn(platform_keys, tmp_path):
    kept: dict[str, Any] = {}
    turn_over = asyncio.Event()

    async def outlive_the_turn() -> Requester | None:
        await turn_over.wait()
        return current_requester()

    async def tool() -> None:
        kept["required"] = platform_client(delegation="required")
        kept["auto"] = platform_client()
        kept["leftover"] = asyncio.create_task(outlive_the_turn())

    manager, _storage = _turn_manager(platform_keys.hosted_settings(tmp_path), tool)

    await _run_turn(manager, caller_assertion=_assertion())
    turn_over.set()

    assert await kept["leftover"] is None
    with pytest.raises(RequesterBindingError) as refused:
        await kept["required"].request("GET", "/api/v1/data-nodes/")
    assert refused.value.code == "requester_binding_invalid"
    manager.backend.platform_request.assert_not_awaited()
    # After the turn, the default client still works, as the Agent: it carries no delegation.
    await kept["auto"].request("GET", "/api/v1/data-nodes/")
    manager.backend.platform_request.assert_awaited_once()
    assert manager.backend.platform_request.await_args.kwargs["proof"] is None
    await _close(manager)


async def test_a_reloaded_turn_start_presents_the_assertion_again(platform_keys, tmp_path):
    manager = SessionRuntimeManager(
        settings=platform_keys.hosted_settings(tmp_path),
        backend=AsyncMock(),
        providers=Mock(),
    )
    stale = SimpleNamespace(
        storage=SimpleNamespace(
            begin_turn=AsyncMock(side_effect=BackendConflictError("stale lease")),
            invalidate_lease=Mock(),
        ),
        activity_sequence=0,
    )
    fresh = SimpleNamespace(storage=_storage(answered_requester=OWNER_UID), activity_sequence=0)
    manager.evict = AsyncMock()  # type: ignore[method-assign]
    manager.get = AsyncMock(return_value=fresh)  # type: ignore[method-assign]

    selected = await manager._begin_turn_with_one_reload(
        session_uid=SESSION_UID,
        runtime=stale,  # type: ignore[arg-type]
        turn_uid="turn-1",
        caller_assertion=_assertion(),
    )

    assert selected is fresh
    assert stale.storage.begin_turn.await_args.kwargs["caller_assertion"] == RAW_ASSERTION
    fresh.storage.begin_turn.assert_awaited_once_with(
        turn_uid="turn-1",
        activity_sequence=1,
        caller_assertion=RAW_ASSERTION,
    )
    assert fresh.requester_user_uid == OWNER_UID
    await manager.aclose()


# --- Hosted routes: which turns present the request's assertion --------------------------------


def _session(uid: str, owner: str) -> AgentSession:
    return AgentSession(
        uid=uid,
        agent_uid="agent-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.4.2",
        llm_provider="openai",
        llm_model="gpt-5.4",
        created_by_user_uid=owner,
    )


def _task(status: str = "submitted") -> AgentTask:
    return AgentTask(
        uid="backend-task-1",
        task_id="task-1",
        context_id=SESSION_UID,
        agent_uid="agent-1",
        agent_session_uid=SESSION_UID,
        status=status,
        status_timestamp=datetime(2026, 10, 6, tzinfo=UTC),
        latest_message={
            "messageId": "message-1",
            "role": A2AMessageDirection.REQUESTER.value,
            "parts": [{"text": "Show my rows."}],
        },
    )


def _dispatchable(manager: _RecordingManager) -> None:
    """Let the dispatch find its Task submitted, as the platform signals it."""

    manager.backend.get_task.side_effect = [_task("submitted"), *[_task("working")] * 20]


class _RecordingManager:
    """Records what each turn receives: its assertion, and whether it is a Task attempt."""

    holder_id = "holder-1"
    draining = False

    def __init__(self, config: TauSDKSettings, client: AsyncMock) -> None:
        self.settings = config
        self.backend = client
        self.turns: list[dict[str, Any]] = []
        self.background: list[Any] = []
        # Answers for successive turns; the last one repeats.
        self.answers = ["Answer."]

    async def prompt(self, session_uid: str, _prompt: str, *, provenance=None, **options: Any):
        task = active_task_execution()
        assertion = options.get("caller_assertion")
        turn = {
            "session_uid": session_uid,
            "assertion": assertion.token if assertion is not None else None,
            "caller": assertion.caller.uid if assertion is not None else None,
            "task_attempt": task is not None,
            "task_caller": task.caller.uid if task is not None and task.caller else None,
        }
        if options.get("caller_delivery") is not None:
            turn["delivery"] = options["caller_delivery"]
        self.turns.append(turn)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        yield TauRuntimeEvent(
            type="message_end",
            data={
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": answer}],
                    "stopReason": "stop",
                }
            },
        )
        yield TauRuntimeEvent(type="agent_settled")

    async def task_execution_fence(self, _session_uid: str):
        return SimpleNamespace(holder_id=self.holder_id, lease_token=LEASE_TOKEN)

    def create_a2a_task_execution(self, _task_uid: str, coroutine, *, name: str):
        del name
        self.background.append(coroutine)
        return None, True

    def create_a2a_caller_delivery(self, _delivery_uid: str, coroutine, *, name: str):
        del name
        self.background.append(coroutine)
        return None, True

    def session_turn_active(self, _session_uid: str) -> bool:
        return False

    async def persist_platform_event(self, *_args: object, **_kwargs: object) -> bool:
        return True

    async def cancel(self, _session_uid: str) -> bool:
        return True

    async def evict(self, _session_uid: str) -> None:
        return None

    def mark_response_delivered(self, _session_uid: str) -> bool:
        return True

    async def run_background(self) -> None:
        for coroutine in self.background:
            await coroutine
        self.background.clear()


def _platform_client(config: TauSDKSettings, *, owner: str = OWNER_UID) -> AsyncMock:
    client = AsyncMock()
    client.settings = config
    working = _task("working")
    client.get_session.side_effect = lambda uid: _session(uid, owner)
    client.create_task.return_value = AgentTaskCreateResult(task=_task(), created=True)
    client.get_task.return_value = working
    client.get_task_by_protocol_id.return_value = working
    client.list_task_messages.return_value = []
    client.list_task_dispatches.return_value = [
        AgentTaskDispatch(uid="dispatch-1", state="pending")
    ]
    attempt = AgentTaskExecutionAttempt(
        uid="attempt-1",
        dispatch_uid="dispatch-1",
        attempt_number=1,
        state="claimed",
    )
    client.claim_task_dispatch.return_value = attempt
    client.start_task_attempt.return_value = attempt.model_copy(update={"state": "running"})
    client.settle_task_attempt.return_value = attempt.model_copy(update={"state": "completed"})
    client.create_task_output.return_value = {"uid": "output-1", "revision": 1}
    client.append_task_output.return_value = {"uid": "output-1", "revision": 2}
    client.finalize_task_output.return_value = {"uid": "output-1", "revision": 2}
    client.get_agent_card.return_value = AgentCardEnvelope(
        agent_session_uid=SESSION_UID,
        agent_uid="agent-1",
        agent_card={
            "name": "Analyst",
            "capabilities": {
                "extensions": [
                    {
                        "uri": RESPONSE_KIND_EXTENSION_URI,
                        "params": {"supportedResponseKinds": ["message", "task"]},
                    }
                ]
            },
        },
    )
    return client


def _hosted_routes(
    platform_keys,
    tmp_path: Path,
    *,
    owner: str = OWNER_UID,
) -> tuple[Any, _RecordingManager]:
    config = platform_keys.hosted_settings(tmp_path)
    app = create_app(config)
    client = _platform_client(config, owner=owner)
    manager = _RecordingManager(config, client)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: config
    return app, manager


def _message(**extra: Any) -> dict[str, Any]:
    return {
        "message": {
            "messageId": "message-1",
            "role": "ROLE_USER",
            "contextId": SESSION_UID,
            "parts": [{"text": "Show my rows."}],
        },
        **extra,
    }


def _rpc(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": "rpc-1", "method": method, "params": params}


MESSAGE_TURN_REQUESTS = {
    "chat": ("/api/chat", {"sessionUid": SESSION_UID, "message": "Show my rows."}),
    "message_send": (f"{REST_BASE}/message:send", _message()),
    "rpc_message_send": ("/api/a2a/rpc", _rpc("message/send", _message())),
    # A strict JSON answer that needs a repair runs a second turn for the same request.
    "message_send_with_repair": (
        f"{REST_BASE}/message:send",
        _message(
            configuration={"acceptedOutputModes": ["application/json"]},
            jsonRepair={"attempts": 1},
        ),
    ),
}


@pytest.mark.parametrize("route", sorted(MESSAGE_TURN_REQUESTS))
@pytest.mark.parametrize("caller", ["person", "agent"])
async def test_hosted_chat_and_message_turns_receive_their_verified_assertion(
    served_platform_keys,
    tmp_path,
    asgi_client,
    route,
    caller,
):
    path, body = MESSAGE_TURN_REQUESTS[route]
    caller_uid = OWNER_UID if caller == "person" else AGENT_USER_UID
    app, manager = _hosted_routes(served_platform_keys, tmp_path, owner=caller_uid)
    if route == "message_send_with_repair":
        manager.answers = ["Not JSON yet.", '{"rows": 2}']
    raw = served_platform_keys.caller_assertion(user_uid=caller_uid)
    identity = USER_CALLER_HEADERS if caller == "person" else AGENT_CALLER_HEADERS

    async with asgi_client(app) as http:
        response = await http.post(path, json=body, headers={**identity, ASSERTION_HEADER: raw})

    assert response.status_code == 200, response.text
    assert len(manager.turns) == (2 if route == "message_send_with_repair" else 1)
    for turn in manager.turns:
        assert turn["assertion"] == raw
        assert turn["caller"] == caller_uid
        assert turn["task_attempt"] is False
    assert raw not in response.text


TASK_TURN_REQUESTS = {
    "message_send_task": (
        f"{REST_BASE}/message:send",
        _message(
            taskId="task-1",
            configuration={"responseKind": "task", "returnImmediately": True},
        ),
    ),
    "message_stream": (f"{REST_BASE}/message:stream", _message(taskId="task-1")),
    "rpc_message_stream": ("/api/a2a/rpc", _rpc("message/stream", _message(taskId="task-1"))),
}


@pytest.mark.parametrize("route", sorted(TASK_TURN_REQUESTS))
async def test_task_attempt_turns_never_receive_the_request_assertion(
    served_platform_keys,
    tmp_path,
    asgi_client,
    route,
):
    path, body = TASK_TURN_REQUESTS[route]
    app, manager = _hosted_routes(served_platform_keys, tmp_path)
    raw = served_platform_keys.caller_assertion()
    headers = {
        **USER_CALLER_HEADERS,
        ASSERTION_HEADER: raw,
        "A2A-Extensions": RESPONSE_KIND_EXTENSION_URI,
    }

    async with asgi_client(app) as http:
        response = await http.post(path, json=body, headers=headers)
    await manager.run_background()

    assert response.status_code == 200, response.text
    assert [turn["task_attempt"] for turn in manager.turns] == [True]
    # The turn start of a Task attempt never presents the assertion. The attempt keeps the
    # request's verified caller only for the teams of the person the platform names.
    assert manager.turns[0]["assertion"] is None
    assert manager.turns[0]["task_caller"] == OWNER_UID


@pytest.mark.parametrize("hosted", [True, False])
async def test_the_platform_dispatch_is_not_a_source_of_the_person(
    served_platform_keys,
    tmp_path,
    asgi_client,
    hosted,
):
    config = (
        served_platform_keys.hosted_settings(tmp_path) if hosted else _managed_settings(tmp_path)
    )
    app = create_app(config)
    client = _platform_client(config)
    manager = _RecordingManager(config, client)
    _dispatchable(manager)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: config
    headers = {ASSERTION_HEADER: served_platform_keys.platform_assertion()} if hosted else {}

    async with asgi_client(app) as http:
        response = await http.post(
            "/internal/a2a/task-dispatch",
            json={
                "task_uid": "backend-task-1",
                "dispatch_uid": "dispatch-1",
                "requester_user_uid": OWNER_UID,
                "requester_identity_type": "human",
            },
            headers=headers,
        )
    await manager.run_background()

    assert response.status_code == 200, response.text
    # The attempt's turn start answers with the person; the dispatch names nobody for it.
    assert manager.turns == [
        {
            "session_uid": SESSION_UID,
            "assertion": None,
            "caller": None,
            "task_attempt": True,
            "task_caller": None,
        }
    ]


# --- platform_client(): calls for the work ------------------------------------------------------

HOLDER_ID = "runtime-host:holder-1"
THE_REQUESTER = Requester(uid=OWNER_UID, team_uids=(TEAM_UID,))
RPC_URL = "https://analyst-data.test/rpc"
BINDING_HEADERS = {
    "x-mainsequence-acting-for-session": SESSION_UID,
    "x-mainsequence-lease-holder": HOLDER_ID,
    "x-mainsequence-lease-token": LEASE_TOKEN,
}


class _RuntimeAuth:
    """The runtime's own credential, refreshed when the platform answers 401."""

    def __init__(self) -> None:
        self.forced = 0

    def bind_client(self, client: httpx.AsyncClient) -> None:
        del client

    async def headers(self, *, force: bool = False) -> dict[str, str]:
        self.forced += int(force)
        return {"Authorization": f"Bearer {RUNTIME_ACCESS_TOKEN}"}

    async def prefetch(self) -> None:
        return None


class _FakePlatform:
    """The platform's API: calls for the work and release access."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.release_tokens = [_release_token(marker="first"), _release_token(marker="second")]
        self.answers: dict[str, list[httpx.Response]] = {}

    def answer(self, path: str, *responses: httpx.Response) -> None:
        self.answers[path] = list(responses)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # The configured base URL is https://platform.test/base.
        path = request.url.path.removeprefix("/base")
        queued = self.answers.get(path)
        if queued:
            return queued.pop(0) if len(queued) > 1 else queued[0]
        if path == f"/api/v1/resource-releases/{RELEASE_UID}/resolve-runtime-access/":
            token = (
                self.release_tokens.pop(0)
                if len(self.release_tokens) > 1
                else (self.release_tokens[0])
            )
            return httpx.Response(200, json=_release_access(token))
        return httpx.Response(200, json={"results": [{"uid": "data-node-1"}]})


def _release_access(token: str, *, mode: str = "token") -> dict[str, Any]:
    return {
        "resource_release_uid": RELEASE_UID,
        "release_kind": "fastapi",
        "routing": {"state": "routable", "active_revision_uid": None},
        "runtime_access": {
            "state": "ready",
            "can_request": True,
            "notice": None,
            "operation": None,
            "retry_after_ms": None,
        },
        "runtime_presence": {"phase": "running"},
        "access": {
            "release_kind": "fastapi",
            "mode": mode,
            "token": token,
            "rpc_url": RPC_URL,
        },
    }


class _FakeApplication:
    """Another platform application, which answers as the requester."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answers: list[httpx.Response] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.answers:
            return self.answers.pop(0)
        return httpx.Response(200, json={"rows": [{"region": "EMEA", "revenue": 12}]})


@asynccontextmanager
async def _bound_turn(
    tmp_path: Path,
    *,
    requester: Requester | None = THE_REQUESTER,
) -> AsyncIterator[tuple[_FakePlatform, _FakeApplication, TurnRequesterBinding, _RuntimeAuth]]:
    config = TauSDKSettings(
        _env_file=None,
        backend_url="https://platform.test/base",
        runtime_credential_id="credential-id",
        workspace=tmp_path,
        startup_dependencies_enabled=False,
    )
    platform = _FakePlatform()
    application = _FakeApplication()
    auth = _RuntimeAuth()
    platform_http = httpx.AsyncClient(
        base_url=config.backend_url,
        transport=httpx.MockTransport(platform.handle),
    )
    application_http = httpx.AsyncClient(transport=httpx.MockTransport(application.handle))
    client = MainSequenceClient(config, auth, client=platform_http)  # type: ignore[arg-type]
    binding = TurnRequesterBinding(
        session_uid=SESSION_UID,
        requester=requester,
        lease_proof=lambda: LeaseProof(
            session_uid=SESSION_UID,
            holder_id=HOLDER_ID,
            lease_token=LEASE_TOKEN,
        ),
        platform=client,
        application_http=lambda: application_http,
    )
    token = bind_turn_requester(binding)
    try:
        yield platform, application, binding, auth
    finally:
        unbind_turn_requester(binding, token)
        await platform_http.aclose()
        await application_http.aclose()


def _secret_headers(request: httpx.Request) -> dict[str, str]:
    return {
        name: value
        for name, value in request.headers.items()
        if name.lower() == "authorization" or name.lower().startswith("x-mainsequence-")
    }


async def test_a_required_delegation_is_refused_before_anything_is_sent(tmp_path):
    async with _bound_turn(tmp_path, requester=None) as (platform, application, _binding, _):
        assert current_requester() is None
        client = platform_client(delegation="required")
        with pytest.raises(RequesterBindingError) as refused:
            await client.request("GET", "/api/v1/data-nodes/")
        with pytest.raises(PermissionError, match="serves nobody"):
            await client.call_release(RELEASE_UID, "GET", "/tables")

    assert refused.value.code == "requester_binding_invalid"
    assert platform.requests == []
    assert application.requests == []


@pytest.mark.parametrize(
    ("requester", "delegation"),
    [
        pytest.param(None, "auto", id="the_turn_serves_nobody"),
        pytest.param(THE_REQUESTER, "none", id="the_tool_chose_none"),
    ],
)
async def test_a_call_without_the_delegation_is_the_agents_own(tmp_path, requester, delegation):
    async with _bound_turn(tmp_path, requester=requester) as (platform, application, _binding, _):
        client = platform_client(delegation=delegation)
        answer = await client.request("GET", "/api/v1/data-nodes/")
        await client.call_release(RELEASE_UID, "GET", "/tables")

    assert answer.status_code == 200
    data_nodes, resolve = platform.requests
    for request in (data_nodes, resolve):
        assert _secret_headers(request) == {"authorization": f"Bearer {RUNTIME_ACCESS_TOKEN}"}
    assert resolve.url.path == (
        f"/base/api/v1/resource-releases/{RELEASE_UID}/resolve-runtime-access/"
    )
    [sent] = application.requests
    assert _secret_headers(sent) == {"authorization": f"Bearer {_release_token(marker='first')}"}


async def test_outside_a_turn_calls_go_through_the_runtime_without_a_delegation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(requester_module, "_PROCESS_BINDING", None)
    with pytest.raises(TauSDKError, match="Agent runtime"):
        platform_client()

    async with _bound_turn(tmp_path) as (platform, application, binding, _auth):
        runtime = register_runtime_platform(
            platform=binding._platform,
            application_http=binding._application_http,
        )
        # A task the runtime detached from its turn has no turn binding.
        requester_module.detach_turn_requester()
        try:
            answer = await platform_client().request("GET", "/api/v1/data-nodes/")
            with pytest.raises(RequesterBindingError):
                await platform_client(delegation="required").request("GET", "/api/v1/data-nodes/")
        finally:
            release_runtime_platform(runtime)

    assert answer.status_code == 200
    [sent] = platform.requests
    assert _secret_headers(sent) == {"authorization": f"Bearer {RUNTIME_ACCESS_TOKEN}"}
    assert application.requests == []
    assert requester_module._PROCESS_BINDING is None


async def test_the_runtime_carries_calls_made_outside_a_turn_until_it_closes(
    platform_keys,
    tmp_path,
):
    manager = SessionRuntimeManager(
        settings=platform_keys.hosted_settings(tmp_path),
        backend=AsyncMock(),
        providers=Mock(),
    )
    assert requester_module._PROCESS_BINDING is manager._process_binding

    await platform_client().request("GET", "/api/v1/data-nodes/")
    await manager.aclose()

    manager.backend.platform_request.assert_awaited_once()
    assert manager.backend.platform_request.await_args.kwargs["proof"] is None
    assert requester_module._PROCESS_BINDING is None


def test_an_unknown_delegation_is_rejected():
    with pytest.raises(ValueError, match="delegation must be one of"):
        platform_client(delegation="always")  # type: ignore[arg-type]


async def test_a_platform_call_carries_the_binding_beside_the_runtime_credential(tmp_path):
    async with _bound_turn(tmp_path) as (platform, application, _binding, _auth):
        assert current_requester() == THE_REQUESTER
        response = await platform_client().request(
            "get",
            "/api/v1/data-nodes/",
            params={"search": "revenue"},
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 200
    assert response.json() == {"results": [{"uid": "data-node-1"}]}
    [sent] = platform.requests
    assert sent.method == "GET"
    assert str(sent.url) == "https://platform.test/base/api/v1/data-nodes/?search=revenue"
    assert _secret_headers(sent) == {
        "authorization": f"Bearer {RUNTIME_ACCESS_TOKEN}",
        **BINDING_HEADERS,
    }
    assert sent.headers["accept"] == "application/json"
    # The answer the tool receives carries no credential and no proof.
    assert _secret_headers(response.request) == {}
    assert application.requests == []


async def test_a_platform_call_retries_once_with_a_refreshed_runtime_credential(tmp_path):
    async with _bound_turn(tmp_path) as (platform, _application, _binding, auth):
        platform.answer(
            "/api/v1/data-nodes/",
            httpx.Response(401, json={"detail": "Token expired."}),
            httpx.Response(200, json={"results": []}),
        )
        response = await platform_client().request("GET", "/api/v1/data-nodes/")

    assert response.status_code == 200
    assert auth.forced == 1
    assert len(platform.requests) == 2


@pytest.mark.parametrize(
    "path",
    [
        "https://elsewhere.test/api/v1/data-nodes/",
        "//elsewhere.test/api/v1/data-nodes/",
        "api/v1/data-nodes/",
        "/api/v1/data nodes/",
        "/api\\v1",
    ],
)
async def test_a_platform_call_never_leaves_its_origin(tmp_path, path):
    async with _bound_turn(tmp_path) as (platform, application, _binding, _auth):
        client = platform_client()
        with pytest.raises(ValueError, match="never a URL"):
            await client.request("GET", path)
        with pytest.raises(ValueError, match="never a URL"):
            await client.call_release(RELEASE_UID, "GET", path)

    assert platform.requests == []
    assert application.requests == []


@pytest.mark.parametrize(
    "header",
    ["Authorization", "proxy-authorization", "X-MainSequence-Lease-Token", ASSERTION_HEADER],
)
async def test_a_tool_cannot_set_the_credential_or_the_binding(tmp_path, header):
    async with _bound_turn(tmp_path) as (platform, _application, _binding, _auth):
        with pytest.raises(ValueError, match="sets"):
            await platform_client().request("GET", "/api/v1/data-nodes/", headers={header: "x"})

    assert platform.requests == []


async def test_a_release_call_obtains_access_for_the_requester_and_sends_only_its_token(tmp_path):
    async with _bound_turn(tmp_path) as (platform, application, _binding, _auth):
        client = platform_client()
        first = await client.call_release(
            RELEASE_UID,
            "POST",
            "/query",
            json={"question": "revenue by region"},
        )
        second = await platform_client().call_release(RELEASE_UID, "GET", "/tables")

    assert first.json() == {"rows": [{"region": "EMEA", "revenue": 12}]}
    assert second.status_code == 200
    # One access request for the turn: the token is kept while it is valid.
    [resolve] = platform.requests
    assert resolve.method == "POST"
    assert resolve.url.path == (
        f"/base/api/v1/resource-releases/{RELEASE_UID}/resolve-runtime-access/"
    )
    assert resolve.content == b""
    assert _secret_headers(resolve) == {
        "authorization": f"Bearer {RUNTIME_ACCESS_TOKEN}",
        **BINDING_HEADERS,
    }
    release_token = _release_token(marker="first")
    assert [str(request.url) for request in application.requests] == [
        f"{RPC_URL}/query",
        f"{RPC_URL}/tables",
    ]
    for request in application.requests:
        # The application receives only its own token: never the runtime credential or the proof.
        assert _secret_headers(request) == {"authorization": f"Bearer {release_token}"}
    assert json.loads(application.requests[0].content) == {"question": "revenue by region"}
    for answer in (first, second):
        assert _secret_headers(answer.request) == {}
        assert release_token not in repr(answer.request.headers)


async def test_a_release_token_near_its_expiry_is_replaced(tmp_path):
    async with _bound_turn(tmp_path) as (platform, application, _binding, _auth):
        platform.release_tokens = [
            _release_token(lifetime_seconds=5, marker="expiring"),
            _release_token(marker="fresh"),
        ]
        client = platform_client()
        await client.call_release(RELEASE_UID, "GET", "/tables")
        await client.call_release(RELEASE_UID, "GET", "/tables")
        await client.call_release(RELEASE_UID, "GET", "/tables")

    assert len(platform.requests) == 2
    assert [request.headers["authorization"] for request in application.requests] == [
        f"Bearer {_release_token(lifetime_seconds=5, marker='expiring')}",
        f"Bearer {_release_token(marker='fresh')}",
        f"Bearer {_release_token(marker='fresh')}",
    ]


async def test_a_rejected_release_token_is_replaced_once(tmp_path):
    async with _bound_turn(tmp_path) as (platform, application, _binding, _auth):
        application.answers = [
            httpx.Response(401, json={"detail": "Token revoked."}),
            httpx.Response(200, json={"rows": []}),
            httpx.Response(401, json={"detail": "Token revoked."}),
            httpx.Response(401, json={"detail": "Token revoked."}),
        ]
        platform.release_tokens = [
            _release_token(marker="first"),
            _release_token(marker="second"),
            _release_token(marker="third"),
        ]
        client = platform_client()
        recovered = await client.call_release(RELEASE_UID, "GET", "/tables")
        refused = await client.call_release(RELEASE_UID, "GET", "/tables")

    assert recovered.status_code == 200
    # A second 401 in a row is the application's answer.
    assert refused.status_code == 401
    assert len(platform.requests) == 3
    assert len(application.requests) == 4


@pytest.mark.parametrize("code", ["requester_binding_invalid", "runtime_lease_expired"])
@pytest.mark.parametrize("where", ["platform", "release_access", "application"])
async def test_an_ended_binding_raises_a_typed_error_and_is_never_retried_as_the_agent(
    tmp_path, code, where
):
    refusal = httpx.Response(403, json={"code": code, "detail": "Requester binding ended."})
    async with _bound_turn(tmp_path) as (platform, application, _binding, _auth):
        if where == "platform":
            platform.answer("/api/v1/data-nodes/", refusal)
        elif where == "release_access":
            platform.answer(
                f"/api/v1/resource-releases/{RELEASE_UID}/resolve-runtime-access/",
                refusal,
            )
        else:
            application.answers = [refusal]
        client = platform_client()
        with pytest.raises(RequesterBindingError) as raised:
            if where == "platform":
                await client.request("GET", "/api/v1/data-nodes/")
            else:
                await client.call_release(RELEASE_UID, "GET", "/tables")

    assert isinstance(raised.value, PermissionError)
    assert raised.value.code == code
    assert code in str(raised.value)
    assert LEASE_TOKEN not in str(raised.value)
    assert RUNTIME_ACCESS_TOKEN not in str(raised.value)
    # One delegated platform request, never a second one as the Agent.
    (delegated,) = platform.requests
    assert BINDING_HEADERS.items() <= _secret_headers(delegated).items()
    assert len(application.requests) == (1 if where == "application" else 0)


async def test_another_refusal_is_the_answer_of_the_platform_or_application(tmp_path):
    forbidden = httpx.Response(403, json={"code": "permission_denied", "detail": "Not shared."})
    async with _bound_turn(tmp_path) as (platform, application, _binding, _auth):
        platform.answer("/api/v1/data-nodes/", forbidden)
        application.answers = [httpx.Response(403, json={"detail": "Not yours."})]
        client = platform_client()
        platform_answer = await client.request("GET", "/api/v1/data-nodes/")
        application_answer = await client.call_release(RELEASE_UID, "GET", "/tables")

    assert platform_answer.status_code == 403
    assert platform_answer.json()["code"] == "permission_denied"
    assert application_answer.status_code == 403


@pytest.mark.parametrize(
    "access",
    [
        pytest.param(None, id="not_ready"),
        pytest.param(_release_access("signed-url", mode="url")["access"], id="url_mode"),
        pytest.param({"release_kind": "fastapi"}, id="malformed"),
    ],
)
async def test_a_release_without_token_access_is_reported_without_its_token(tmp_path, access):
    answer = {**_release_access("never-shown-release-token"), "access": access}
    if access is not None and "mode" not in access:
        access["token"] = "never-shown-release-token"
    async with _bound_turn(tmp_path) as (platform, application, _binding, _auth):
        platform.answer(
            f"/api/v1/resource-releases/{RELEASE_UID}/resolve-runtime-access/",
            httpx.Response(200, json=answer),
        )
        with pytest.raises(BackendError) as raised:
            await platform_client().call_release(RELEASE_UID, "GET", "/tables")

    assert "never-shown-release-token" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert application.requests == []


async def test_the_turns_delegation_and_release_access_end_with_it(tmp_path):
    async with _bound_turn(tmp_path) as (platform, application, binding, _auth):
        required = platform_client(delegation="required")
        default = platform_client()
        await required.call_release(RELEASE_UID, "GET", "/tables")
        binding.close()
        assert current_requester() is None
        with pytest.raises(RequesterBindingError):
            await required.call_release(RELEASE_UID, "GET", "/tables")
        with pytest.raises(RequesterBindingError):
            await required.request("GET", "/api/v1/data-nodes/")
        # The default client goes on as the Agent, with a token of its own.
        await default.call_release(RELEASE_UID, "GET", "/tables")

    delegated_resolve, own_resolve = platform.requests
    assert _secret_headers(delegated_resolve) == {
        "authorization": f"Bearer {RUNTIME_ACCESS_TOKEN}",
        **BINDING_HEADERS,
    }
    assert _secret_headers(own_resolve) == {"authorization": f"Bearer {RUNTIME_ACCESS_TOKEN}"}
    assert len(application.requests) == 2
    assert repr(required) == "PlatformClient(delegation='required')"
    assert LEASE_TOKEN not in repr(binding)


async def test_a_release_is_named_by_its_canonical_uid(tmp_path):
    async with _bound_turn(tmp_path) as (platform, _application, _binding, _auth):
        with pytest.raises(ValueError, match="canonical"):
            await platform_client().call_release("../../admin", "GET", "/tables")

    assert platform.requests == []


# --- End to end: a hosted chat turn whose project tool acts for its requester -----------------

PROBE_EXTENSION = f'''"""Read for the person this turn serves, and return business results only."""

from __future__ import annotations

from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult

from ms_tau_sdk import current_requester, platform_client


async def probe(tool_call_id, arguments, signal=None, on_update=None):
    del tool_call_id, arguments, signal, on_update
    requester = current_requester()
    client = platform_client()
    platform = await client.request("GET", "/api/v1/requester-probe/")
    release = await client.call_release(
        "{RELEASE_UID}", "GET", "/answers", params={{"q": "revenue"}}
    )
    text = (
        f"requester={{requester.uid}} teams={{','.join(requester.team_uids)}} "
        f"platform={{platform.json()['answer']}} release={{release.json()['answer']}}"
    )
    return AgentToolResult(content=[TextContent(text=text)], details={{"rows": 2}})


def setup(tau) -> None:
    tau.register_tool(
        AgentTool(
            name="requester_probe",
            label="Requester Probe",
            description="Read data for the person this turn serves.",
            parameters={{"type": "object", "properties": {{}}, "additionalProperties": False}},
            execute_fn=probe,
        )
    )
'''


class _HostedPlatform:
    """The platform of a hosted runtime: its session, turns, entries, and requester reads."""

    def __init__(self, *, release_token: str) -> None:
        self.release_token = release_token
        self.activity: list[httpx.Request] = []
        self.housekeeping: list[httpx.Request] = []
        self.requester_bound: list[httpx.Request] = []
        self.persisted: list[Any] = []
        self.revision = 1

    def handle(self, request: httpx.Request) -> httpx.Response:
        session = f"/api/v1/agent-sessions/{SESSION_UID}/"
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path.startswith(session):
            self.housekeeping.append(request)
        if request.method == "GET" and path == session:
            return httpx.Response(200, json=self._session())
        if request.method == "GET" and path == f"{session}agent-card/":
            return httpx.Response(
                200, json={"agent_session_uid": SESSION_UID, "agent_uid": "agent-1"}
            )
        if path == f"{session}tau-runtime/bootstrap/":
            return httpx.Response(200, json=self._bootstrap(body["holder_id"]))
        if path == f"{session}tau-runtime-activity/":
            self.activity.append(request)
            # The platform verifies the presented assertion and records its person as the turn's
            # requester: here, the session's owner.
            presented = ASSERTION_HEADER in request.headers
            starting = body["runtime_activity"] == "working"
            return httpx.Response(
                200,
                json=self._state(body, requester=OWNER_UID if presented and starting else None),
            )
        if path == f"{session}entries/append-batch/":
            return httpx.Response(201, json=self._append(body))
        if path == f"{session}tau-runtime/resume-snapshot/":
            self.persisted.append(body["snapshot"])
            return httpx.Response(200, json=self._snapshot(body))
        if path.startswith(f"{session}runtime-lease/"):
            return httpx.Response(200, json=self._lease(body.get("holder_id", "")))
        if path == "/api/v1/requester-probe/":
            self.requester_bound.append(request)
            return httpx.Response(200, json={"answer": "platform-rows"})
        if path == f"/api/v1/resource-releases/{RELEASE_UID}/resolve-runtime-access/":
            self.requester_bound.append(request)
            access = _release_access(self.release_token)
            access["access"]["rpc_url"] = "https://analyst-data.test/rpc"
            return httpx.Response(200, json=access)
        return httpx.Response(404, json={"detail": f"No fake for {request.method} {path}"})

    @staticmethod
    def _session() -> dict[str, Any]:
        return {
            "uid": SESSION_UID,
            "agent_uid": "agent-1",
            "harness": "tau",
            "harness_protocol": "tau-session-v1",
            "harness_version": "0.4.2",
            "llm_provider": "openai",
            "llm_model": "gpt-5.4",
            "created_by_user_uid": OWNER_UID,
            "runtime_config_sha256": "sha256:runtime",
        }

    @staticmethod
    def _lease(holder_id: str) -> dict[str, Any]:
        return {
            "lease_token": LEASE_TOKEN,
            "holder_id": holder_id,
            "lease_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        }

    def _bootstrap(self, holder_id: str) -> dict[str, Any]:
        return {
            "session": self._session(),
            "lease": self._lease(holder_id),
            "runtime_state": {
                "harness": "tau",
                "harness_protocol": "tau-session-v1",
                "harness_version": "0.4.2",
                "runtime_activity": "loading",
                "activity_revision": 1,
                "activity_sequence": 0,
                "runtime_capabilities": RUNTIME_CAPABILITIES,
            },
            "history": {"entries": [], "next_sequence": 0, "has_more": False},
            "resume_snapshot": None,
            "provider_credentials": {
                "credentials": {
                    "openai": {"credential_kind": "api_key", "credential": {"api_key": "pk"}}
                }
            },
            "provider_control": _provider_control().model_dump(mode="json"),
            "runtime_capabilities": RUNTIME_CAPABILITIES,
        }

    def _state(self, body: dict[str, Any], *, requester: str | None) -> dict[str, Any]:
        self.revision += 1
        return {
            "harness": "tau",
            "harness_protocol": "tau-session-v1",
            "harness_version": "0.4.2",
            "runtime_activity": body["runtime_activity"],
            "active_turn_uid": body.get("active_turn_uid"),
            "activity_sequence": body.get("activity_sequence"),
            "activity_revision": self.revision,
            "applied": True,
            "requester_user_uid": requester,
        }

    def _append(self, body: dict[str, Any]) -> dict[str, Any]:
        expected = body["expected_sequence"]
        turn = body.get("turn") or {}
        records = []
        for offset, item in enumerate(body["entries"]):
            self.persisted.append(item["entry"])
            records.append(
                {
                    "sequence": expected + offset,
                    "entry_type": item["entry"]["type"],
                    "entry_json": item["entry"],
                    "idempotency_key": item["idempotency_key"],
                    "turn_uid": turn.get("turn_uid"),
                }
            )
        answer: dict[str, Any] = {
            "entries": records,
            "next_sequence": expected + len(records),
            "created_count": len(records),
            "replayed": False,
        }
        if turn.get("phase") == "committed":
            answer["turn_commit"] = {
                "turn_uid": turn["turn_uid"],
                "next_sequence": expected + len(records),
                "committed_at": datetime.now(UTC).isoformat(),
            }
        return answer

    @staticmethod
    def _snapshot(body: dict[str, Any]) -> dict[str, Any]:
        return {
            **{key: value for key, value in body.items() if key not in {"snapshot", "holder_id"}},
            "applied": True,
            "replayed": False,
            "canonical_size": 1,
        }


def _provider_control() -> ProviderControl:
    return ProviderControl(
        schema_version=1,
        catalog_digest=f"sha256:{'0' * 64}",
        provider="openai",
        model={
            "model": "gpt-5.4",
            "api": "openai-responses",
            "input": ["text"],
            "reasoning": False,
            "thinking_levels": [],
        },
    )


def _model_turns() -> FakeProvider:
    return FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="toolUse",
                    message=AssistantMessage(
                        model="gpt-5.4",
                        stop_reason="toolUse",
                        content=[ToolCall(id="call-1", name="requester_probe", arguments={})],
                    ),
                )
            ],
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        model="gpt-5.4",
                        stop_reason="stop",
                        content="Your revenue rows are ready.",
                    ),
                )
            ],
        ]
    )


async def test_a_hosted_turn_acts_for_its_requester_and_exposes_no_secret(
    platform_keys,
    runtime_identity_token_file,
    tmp_path,
    asgi_client,
    capsys,
):
    extension = tmp_path / ".tau/extensions/requester_probe/extension.py"
    extension.parent.mkdir(parents=True)
    extension.write_text(PROBE_EXTENSION, encoding="utf-8")
    config = platform_keys.hosted_settings(
        tmp_path,
        exclude_base_tools=True,
        exclude_mainsequence_mcp=True,
        runtime_identity_token_file=runtime_identity_token_file,
        log_level="DEBUG",
    )
    release_token = _release_token(marker="end-to-end")
    platform = _HostedPlatform(release_token=release_token)
    application_requests: list[httpx.Request] = []

    def application(request: httpx.Request) -> httpx.Response:
        application_requests.append(request)
        return httpx.Response(200, json={"answer": "application-rows"})

    model = _model_turns()
    auth = _RuntimeAuth()
    client = MainSequenceClient(config, auth)  # type: ignore[arg-type]
    providers = Mock()
    providers.for_session_credential.return_value = ProviderRuntime(
        name="openai",
        model="gpt-5.4",
        thinking_level="off",
        provider=model,
        credential=ProviderCredential(provider="openai", api_key="pk"),
        provider_control=_provider_control(),
    )
    manager = SessionRuntimeManager(settings=config, backend=client, providers=providers)
    services = ApplicationServices(
        settings=config,
        auth=auth,  # type: ignore[arg-type]
        backend=client,
        providers=providers,
        runtime=manager,
    )
    app = create_app(config, services_factory=lambda _settings: services)
    raw = platform_keys.caller_assertion(team_uids=[TEAM_UID])

    with respx.mock(assert_all_called=False) as router:
        router.get(platform_keys.jwks_url).mock(side_effect=platform_keys.respond)
        router.route(host="backend").mock(side_effect=platform.handle)
        router.route(host="analyst-data.test").mock(side_effect=application)
        async with asgi_client(app, lifespan=True) as http:
            response = await http.post(
                "/api/chat",
                json={"sessionUid": SESSION_UID, "message": "Show my revenue rows."},
                headers={"X-Caller-Kind": "user", ASSERTION_HEADER: raw},
            )
    output = capsys.readouterr()

    assert response.status_code == 200, response.text
    # Only the transition that starts the turn presents the request's assertion.
    started = [
        r for r in platform.activity if json.loads(r.content)["runtime_activity"] == "working"
    ]
    assert [r.headers.get(ASSERTION_HEADER) for r in started] == [raw]
    assert all(
        ASSERTION_HEADER not in request.headers
        for request in platform.activity
        if request not in started
    )
    # The runtime's own housekeeping (turn activity, entries, snapshots, the lease) never carries
    # the person's delegation, even in a turn that serves a person.
    assert platform.housekeeping
    for request in platform.housekeeping:
        assert not set(BINDING_HEADERS) & {name.lower() for name in request.headers}
    # The tool read for the requester, from the platform and from another application.
    assert [request.url.path for request in platform.requester_bound] == [
        "/api/v1/requester-probe/",
        f"/api/v1/resource-releases/{RELEASE_UID}/resolve-runtime-access/",
    ]
    for request in platform.requester_bound:
        assert _secret_headers(request) == {
            "authorization": f"Bearer {RUNTIME_ACCESS_TOKEN}",
            "x-mainsequence-acting-for-session": SESSION_UID,
            "x-mainsequence-lease-holder": manager.holder_id,
            "x-mainsequence-lease-token": LEASE_TOKEN,
        }
    [application_call] = application_requests
    assert str(application_call.url) == "https://analyst-data.test/rpc/answers?q=revenue"
    assert _secret_headers(application_call) == {"authorization": f"Bearer {release_token}"}
    # The model receives the business result only.
    tool_results = [
        message for message in model.calls[1][2] if type(message).__name__ == "ToolResultMessage"
    ]
    assert [result.content[0].text for result in tool_results] == [
        f"requester={OWNER_UID} teams={TEAM_UID} platform=platform-rows release=application-rows"
    ]
    model_context = "".join(
        message.model_dump_json()
        for _model, _system, messages, _tools in model.calls
        for message in messages
    )
    persisted = json.dumps(platform.persisted)
    assert "application-rows" in persisted
    for secret in (raw, LEASE_TOKEN, release_token, RUNTIME_ACCESS_TOKEN):
        assert secret not in output.out
        assert secret not in output.err
        assert secret not in persisted
        assert secret not in response.text
        assert secret not in model_context


async def test_a_caller_delivery_turn_names_its_delivery(
    served_platform_keys,
    tmp_path,
    asgi_client,
):
    app, manager = _hosted_routes(served_platform_keys, tmp_path)

    async with asgi_client(app) as http:
        response = await http.post(
            "/internal/a2a/task-caller-delivery",
            json={
                "delivery_uid": "delivery-1",
                "task_uid": "backend-task-1",
                "task_id": "task-1",
                "status": "completed",
                "caller_agent_session_uid": SESSION_UID,
                "event_cursor": 3,
                "requester_user_uid": OWNER_UID,
                "requester_identity_type": "human",
            },
            headers={ASSERTION_HEADER: served_platform_keys.platform_assertion()},
        )
    await manager.run_background()

    assert response.status_code == 200, response.text
    # No assertion: the platform starts the turn, and the turn names the delivery instead. The
    # platform's answer to that turn start names the person; the signal's facts are not used.
    assert manager.turns == [
        {
            "session_uid": SESSION_UID,
            "assertion": None,
            "caller": None,
            "task_attempt": False,
            "task_caller": None,
            "delivery": CallerDelivery(uid="delivery-1"),
        }
    ]


# --- A Task the request creates or continues -------------------------------------------------


def _answered_task(uid: str | None, identity_type: str | None, status: str = "submitted"):
    return _task(status).model_copy(
        update={"requester_user_uid": uid, "requester_identity_type": identity_type}
    )


@pytest.mark.parametrize("route", sorted(TASK_TURN_REQUESTS))
async def test_a_task_the_request_creates_presents_its_assertion_and_keeps_its_caller(
    served_platform_keys,
    tmp_path,
    asgi_client,
    route,
):
    path, body = TASK_TURN_REQUESTS[route]
    app, manager = _hosted_routes(served_platform_keys, tmp_path)
    # The Task answer's requester is an audit fact; it never decides whom the attempt serves.
    manager.backend.create_task.return_value = AgentTaskCreateResult(
        task=_answered_task(OTHER_UID, "human"),
        created=True,
    )
    raw = served_platform_keys.caller_assertion(team_uids=[TEAM_UID])
    headers = {
        **USER_CALLER_HEADERS,
        ASSERTION_HEADER: raw,
        "A2A-Extensions": RESPONSE_KIND_EXTENSION_URI,
    }

    async with asgi_client(app) as http:
        response = await http.post(path, json=body, headers=headers)
    await manager.run_background()

    assert response.status_code == 200, response.text
    create = manager.backend.create_task.await_args
    assert create.kwargs == {"caller_assertion": raw}
    # The assertion goes only in the header, never into the Task the platform persists.
    assert raw not in json.dumps(create.args[0])
    assert [turn["task_attempt"] for turn in manager.turns] == [True]
    assert manager.turns[0]["task_caller"] == OWNER_UID
    # A Task attempt never presents the assertion when it starts its turn.
    assert manager.turns[0]["assertion"] is None
    assert raw not in response.text


async def test_a_task_the_request_continues_presents_its_assertion_and_keeps_its_caller(
    served_platform_keys,
    tmp_path,
    asgi_client,
):
    app, manager = _hosted_routes(served_platform_keys, tmp_path)
    manager.backend.get_task_by_protocol_id.return_value = _task("input_required")
    manager.backend.continue_task.return_value = _answered_task(None, None, "working")
    raw = served_platform_keys.caller_assertion(team_uids=[TEAM_UID])
    body = _message(configuration={"responseKind": "task", "returnImmediately": True})
    body["message"]["taskId"] = "task-1"

    async with asgi_client(app) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            json=body,
            headers={
                **USER_CALLER_HEADERS,
                ASSERTION_HEADER: raw,
                "A2A-Extensions": RESPONSE_KIND_EXTENSION_URI,
            },
        )
    await manager.run_background()

    assert response.status_code == 200, response.text
    proceed = manager.backend.continue_task.await_args
    assert proceed.args[0] == "backend-task-1"
    assert proceed.kwargs == {"caller_assertion": raw}
    assert raw not in json.dumps(proceed.args[1])
    assert [turn["task_caller"] for turn in manager.turns] == [OWNER_UID]
    assert manager.turns[0]["assertion"] is None


async def test_an_expired_assertion_is_not_presented_for_a_task(tmp_path):
    client = _platform_client(_managed_settings(tmp_path))
    client.create_task.return_value = AgentTaskCreateResult(
        task=_answered_task(OWNER_UID, "human"),
        created=True,
    )
    expired = _assertion(lifetime_seconds=-1)

    await _create_backend_task(
        client,
        message=_message()["message"],
        task_id="task-1",
        local_mode=False,
        caller_assertion=expired,
    )

    assert client.create_task.await_args.kwargs == {}


@pytest.mark.parametrize("mode", ["hosted", "local", "expired", "platform_call"])
def test_only_a_valid_assertion_of_a_hosted_request_is_presented_for_a_task(
    platform_keys,
    tmp_path,
    mode,
):
    hosted = platform_keys.hosted_settings(tmp_path)
    config = hosted.model_copy(update={"local_mode": True}) if mode == "local" else hosted
    assertion = _assertion(lifetime_seconds=-1 if mode == "expired" else 300)
    scope = {} if mode == "platform_call" else {_CALLER_ASSERTION_SCOPE_KEY: assertion}
    request = SimpleNamespace(scope=scope)

    presented = _task_caller_assertion(request, config)  # type: ignore[arg-type]

    assert presented is (assertion if mode == "hosted" else None)
    assert _request_caller(presented) == (assertion.caller if mode == "hosted" else None)


# --- Work the delegating Agent sends: the platform names the person ------------------------------

AGENT_A_UID = AGENT_CALLER_HEADERS["X-Caller-Agent-UID"]
CHILD_SESSION = "55555555-eeee-4fff-8aaa-666666666666"


def _delegated_child_client(config: TauSDKSettings) -> AsyncMock:
    """The platform's real combination: a person-owned child session whose parent is Agent A's."""

    client = _platform_client(config)
    client.get_session.side_effect = lambda uid: _session(uid, OWNER_UID).model_copy(
        update={"parent_session_agent_uid": AGENT_A_UID}
    )
    client.get_user.return_value = DirectoryUser(
        uid=AGENT_USER_UID,
        identity_type="workload",
        agent_uid=AGENT_A_UID,
    )
    return client


@pytest.mark.parametrize("route", ["chat", "message_send"])
@pytest.mark.parametrize(
    ("answered_requester", "expected"),
    [
        pytest.param(None, None, id="nobody"),
        pytest.param(OWNER_UID, Requester(uid=OWNER_UID), id="the_delegating_person"),
    ],
)
async def test_a_delegated_turn_serves_the_person_the_platform_names(
    served_platform_keys,
    tmp_path,
    asgi_client,
    route,
    answered_requester,
    expected,
):
    config = served_platform_keys.hosted_settings(tmp_path)
    client = _delegated_child_client(config)
    seen: list[Requester | None] = []

    async def tool() -> None:
        seen.append(current_requester())

    manager = SessionRuntimeManager(settings=config, backend=client, providers=Mock())
    storage = _storage(answered_requester=answered_requester)
    manager._runtimes[CHILD_SESSION] = ActiveSessionRuntime(
        session_uid=CHILD_SESSION,
        holder_id=manager.holder_id,
        coding_session=_ToolTurnSession(tool),  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        provider=object(),
        provider_name="openai",
        model="gpt-5.4",
    )
    app = create_app(config)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: config
    raw = served_platform_keys.caller_assertion(user_uid=AGENT_USER_UID)
    headers = {**AGENT_CALLER_HEADERS, ASSERTION_HEADER: raw}
    path, body = {
        "chat": ("/api/chat", {"sessionUid": CHILD_SESSION, "message": "Delegated work."}),
        "message_send": (
            f"{REST_BASE}/message:send",
            {
                "message": {
                    "messageId": "message-1",
                    "role": "ROLE_USER",
                    "contextId": CHILD_SESSION,
                    "parts": [{"text": "Delegated work."}],
                }
            },
        ),
    }[route]

    async with asgi_client(app) as http:
        response = await http.post(path, json=body, headers=headers)

    assert response.status_code == 200, response.text
    client.get_user.assert_awaited_once_with(AGENT_USER_UID)
    # The turn presents A's assertion; the platform's answer alone names the person, whoever
    # called. A's own teams never become the person's.
    assert storage.begin_turn.await_args.kwargs["caller_assertion"] == raw
    assert seen == [expected]
    await _close(manager)


async def test_a_task_the_delegating_agent_creates_keeps_its_caller(
    served_platform_keys,
    tmp_path,
    asgi_client,
):
    config = served_platform_keys.hosted_settings(tmp_path)
    app = create_app(config)
    client = _delegated_child_client(config)
    manager = _RecordingManager(config, client)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: config
    client.create_task.return_value = AgentTaskCreateResult(
        task=_answered_task(OWNER_UID, "human").model_copy(
            update={"context_id": CHILD_SESSION, "agent_session_uid": CHILD_SESSION}
        ),
        created=True,
    )
    raw = served_platform_keys.caller_assertion(user_uid=AGENT_USER_UID)
    body = _message(taskId="task-1")
    body["message"]["contextId"] = CHILD_SESSION

    async with asgi_client(app) as http:
        response = await http.post(
            f"{REST_BASE}/message:stream",
            json=body,
            headers={**AGENT_CALLER_HEADERS, ASSERTION_HEADER: raw},
        )

    assert response.status_code == 200, response.text
    assert client.create_task.await_args.kwargs == {"caller_assertion": raw}
    # The attempt keeps A's verified caller only for teams; the platform names the person.
    assert [turn["task_caller"] for turn in manager.turns] == [AGENT_USER_UID]
