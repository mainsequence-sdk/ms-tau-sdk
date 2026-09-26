import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ms_tau_sdk.api.a2a import _reconcile_local_tasks_once
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.local import LocalDevelopmentBackend
from ms_tau_sdk.backend.models import (
    ProviderControl,
    ProviderCredential,
    ProviderExecutionEvidence,
    RuntimeLeaseReleaseRequest,
    SessionEntryBatchAppendRequest,
    SessionEntryBatchItem,
    TauRuntimeBootstrapRequest,
    TauTurnLifecycle,
)
from ms_tau_sdk.errors import BackendConflictError, LocalModeUnsupportedError
from ms_tau_sdk.protocols.a2a_failure import TASK_FAILURE_EXTENSION_URI
from ms_tau_sdk.settings import TauSDKSettings


def _settings(tmp_path) -> TauSDKSettings:
    return TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        local_state_root=tmp_path / "state",
        backend_url="http://backend.test",
        auth_mode="jwt",
        local_mode=True,
        access_token="mainsequence-access-token",
        refresh_token="mainsequence-refresh-token",
        local_provider="openai",
        local_model="gpt-5.4",
        local_thinking="high",
    )


def _evidence() -> ProviderExecutionEvidence:
    return ProviderExecutionEvidence(
        credential=ProviderCredential(
            provider="openai",
            credential_kind="api_key",
            api_key="provider-secret",
        ),
        provider_control=ProviderControl(
            schema_version=1,
            catalog_digest=f"sha256:{'0' * 64}",
            provider="openai",
            model={
                "model": "gpt-5.4",
                "api": "openai-responses",
                "input": ["text", "image"],
                "reasoning": True,
                "thinking_levels": ["high"],
            },
        ),
    )


def _services(evidence: ProviderExecutionEvidence) -> MainSequenceClient:
    services = Mock(spec=MainSequenceClient)
    services.auth = Mock()
    services.hydrate_local_provider_credential = AsyncMock(return_value=evidence)
    services.aclose = AsyncMock()
    return services


def _bootstrap(holder_id: str) -> TauRuntimeBootstrapRequest:
    return TauRuntimeBootstrapRequest(
        holder_id=holder_id,
        ttl_seconds=90,
        bootstrap_request_uid=f"bootstrap-{holder_id}",
        supported_snapshot_schema_versions=[2],
        supported_provider_control_schema_versions=[1],
        tau_runtime_version="test",
    )


@pytest.mark.asyncio
async def test_local_backend_lazily_persists_session_history_and_not_credentials(tmp_path):
    settings = _settings(tmp_path)
    session_uid = settings.local_session_uid(None)
    services = _services(_evidence())
    backend = LocalDevelopmentBackend(settings, services)

    first = await backend.bootstrap_tau_runtime(session_uid, _bootstrap("holder-1"))
    appended = await backend.append_entries(
        session_uid,
        SessionEntryBatchAppendRequest(
            lease_token=first.lease.lease_token,
            holder_id="holder-1",
            expected_sequence=0,
            entries=[
                SessionEntryBatchItem(
                    idempotency_key="entry-1",
                    entry={"type": "label", "label": "Local conversation"},
                )
            ],
            turn=TauTurnLifecycle(
                turn_uid="turn-1",
                phase="started",
                activity_sequence=1,
            ),
        ),
    )
    committed = await backend.append_entries(
        session_uid,
        SessionEntryBatchAppendRequest(
            lease_token=first.lease.lease_token,
            holder_id="holder-1",
            expected_sequence=1,
            entries=[],
            turn=TauTurnLifecycle(
                turn_uid="turn-1",
                phase="committed",
                activity_sequence=2,
            ),
        ),
    )
    await backend.release_runtime_lease(
        session_uid,
        RuntimeLeaseReleaseRequest(
            lease_token=first.lease.lease_token,
            holder_id="holder-1",
            reason="runtime_eviction",
        ),
    )

    restarted_services = _services(_evidence())
    restarted = LocalDevelopmentBackend(settings, restarted_services)
    resumed = await restarted.bootstrap_tau_runtime(session_uid, _bootstrap("holder-2"))

    assert first.session.uid == session_uid
    assert first.session.active_provider == "openai"
    assert first.session.active_model == "gpt-5.4"
    assert first.provider_credentials["credentials"]["openai"]["credential"]["api_key"] == (
        "provider-secret"
    )
    parsed = backend.provider_credential_from_hydration(
        "openai",
        first.provider_credentials,
    )
    assert parsed.provider == "openai"
    assert parsed.secret() == "provider-secret"
    assert appended.next_sequence == 1
    assert committed.turn_commit is not None
    assert resumed.history.next_sequence == 1
    assert resumed.history.entries[0].entry_json["label"] == "Local conversation"
    restarted_services.hydrate_local_provider_credential.assert_awaited_once_with(
        "openai",
        model="gpt-5.4",
        thinking_level="high",
        holder_id="holder-2",
    )

    persisted = b"".join(
        path.read_bytes() for path in settings.local_state_path.parent.glob("runtime.sqlite3*")
    )
    assert b"provider-secret" not in persisted
    assert b"mainsequence-access-token" not in persisted
    assert b"mainsequence-refresh-token" not in persisted

    await backend.aclose()
    await restarted.aclose()


@pytest.mark.asyncio
async def test_local_backend_rejects_platform_orchestration_routes(tmp_path):
    backend = LocalDevelopmentBackend(_settings(tmp_path), _services(_evidence()))

    with pytest.raises(LocalModeUnsupportedError, match="registered Agent"):
        await backend._request("POST", "/api/v1/agent-tasks/")

    await backend.aclose()


@pytest.mark.asyncio
async def test_local_ordinary_turn_does_not_acquire_a_task_owner_or_block_release(tmp_path):
    settings = _settings(tmp_path)
    backend = LocalDevelopmentBackend(settings, _services(_evidence()))
    session_uid = settings.local_session_uid("ordinary-turn")
    bootstrap = await backend.bootstrap_tau_runtime(session_uid, _bootstrap("holder-1"))
    appended = await backend.append_entries(
        session_uid,
        SessionEntryBatchAppendRequest(
            lease_token=bootstrap.lease.lease_token,
            holder_id="holder-1",
            expected_sequence=0,
            entries=[
                SessionEntryBatchItem(
                    idempotency_key="ordinary-entry-1",
                    entry={"type": "custom", "kind": "ordinary"},
                )
            ],
            turn=TauTurnLifecycle(
                turn_uid="ordinary-turn-1",
                phase="started",
                activity_sequence=1,
            ),
        ),
    )

    assert appended.runtime_state is not None
    assert appended.runtime_state.active_turn_uid == "ordinary-turn-1"
    assert appended.runtime_state.active_task_attempt_uid is None
    await backend.release_runtime_lease(
        session_uid,
        RuntimeLeaseReleaseRequest(
            lease_token=bootstrap.lease.lease_token,
            holder_id="holder-1",
            reason="runtime_eviction",
        ),
    )
    await backend.aclose()


@pytest.mark.asyncio
async def test_local_backend_serializes_concurrent_idempotent_writes(tmp_path):
    settings = _settings(tmp_path)
    first_backend = LocalDevelopmentBackend(settings, _services(_evidence()))
    second_backend = LocalDevelopmentBackend(settings, _services(_evidence()))
    session_uid = settings.local_session_uid("concurrent")
    bootstrap = await first_backend.bootstrap_tau_runtime(
        session_uid,
        _bootstrap("holder-1"),
    )
    await second_backend.state_ready()
    request = SessionEntryBatchAppendRequest(
        lease_token=bootstrap.lease.lease_token,
        expected_sequence=0,
        entries=[
            SessionEntryBatchItem(
                idempotency_key="concurrent-entry",
                entry={"type": "label", "label": "Written once"},
            )
        ],
    )

    first, second = await asyncio.gather(
        first_backend.append_entries(session_uid, request),
        second_backend.append_entries(session_uid, request),
    )

    assert sorted([first.created_count, second.created_count]) == [0, 1]
    assert sorted([first.replayed, second.replayed]) == [False, True]
    entries = await first_backend.get_entries(session_uid)
    assert entries.next_sequence == 1
    assert len(entries.entries) == 1

    await first_backend.aclose()
    await second_backend.aclose()


@pytest.mark.asyncio
async def test_local_backend_persists_complete_a2a_task_lifecycle(tmp_path):
    settings = _settings(tmp_path)
    services = _services(_evidence())
    backend = LocalDevelopmentBackend(settings, services)
    session_uid = settings.local_session_uid("a2a-context")
    bootstrap = await backend.bootstrap_tau_runtime(session_uid, _bootstrap("holder-1"))
    payload = {
        "task_id": "task-1",
        "context_id": session_uid,
        "agent_uid": f"local-agent-{settings.workspace_digest}",
        "agent_session_uid": session_uid,
        "initial_message": {
            "messageId": "message-1",
            "role": "ROLE_REQUESTER",
            "parts": [{"text": "Do local work."}],
        },
        "metadata": {"transport": "a2a"},
    }

    created = await backend.create_task(payload)
    replayed = await backend.create_task(payload)
    dispatch = (await backend.list_task_dispatches(created.task.uid))[0]
    attempt = await backend.claim_task_dispatch(
        created.task.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        dispatch_uid=dispatch.uid,
    )
    attempt = await backend.start_task_attempt(
        created.task.uid,
        attempt_uid=attempt.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        turn_uid="turn-task-1",
    )
    first_output = await backend.create_task_output(
        created.task.uid,
        attempt_uid=attempt.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        artifact_id="artifact-1",
        parts=[{"text": "Local "}],
        name="Agent response",
    )
    final_output = await backend.append_task_output(
        created.task.uid,
        attempt_uid=attempt.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        output_uid=first_output["uid"],
        parts=[{"text": "result."}],
        expected_revision=1,
        last_chunk=True,
    )
    await backend.append_entries(
        session_uid,
        SessionEntryBatchAppendRequest(
            lease_token=bootstrap.lease.lease_token,
            holder_id="holder-1",
            expected_sequence=0,
            entries=[],
            turn=TauTurnLifecycle(
                turn_uid="turn-task-1",
                phase="committed",
                activity_sequence=1,
            ),
        ),
    )
    completed_attempt = await backend.settle_task_attempt(
        created.task.uid,
        attempt_uid=attempt.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        status="completed",
    )
    completed = await backend.get_task(created.task.uid)
    snapshot = await backend.get_task_snapshot(created.task.uid)
    events = await backend.list_task_events(
        created.task.uid,
        after_sequence=0,
        limit=100,
    )

    assert created.created is True
    assert replayed.created is False
    assert replayed.task.uid == created.task.uid
    assert attempt.attempt_number == 1
    assert first_output["revision"] == 1
    assert final_output["revision"] == 2
    assert completed_attempt.state == "completed"
    assert completed.status == "completed"
    assert completed.latest_message is not None
    assert completed.latest_message["messageId"] == "message-1"
    assert completed.outputs[0]["parts"] == [
        {"text": "Local "},
        {"text": "result."},
    ]
    assert snapshot.event_cursor == completed.last_event_sequence
    assert events.next_cursor == completed.last_event_sequence
    assert events.events[-1].event_type == "status_changed"
    assert events.events[-1].status == "completed"

    restarted = LocalDevelopmentBackend(settings, _services(_evidence()))
    persisted = await restarted.get_task_by_protocol_id("task-1")
    listed = await restarted.list_tasks(context_id="a2a-context")
    assert persisted.status == "completed"
    assert [task.task_id for task in listed] == ["task-1"]
    with sqlite3.connect(settings.local_state_path) as connection:
        schema_version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        task_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(a2a_tasks)").fetchall()
        }
    assert schema_version == "4"
    assert {"failure_code", "recovery_owner", "terminal_at"} <= task_columns

    await backend.aclose()
    await restarted.aclose()


@pytest.mark.asyncio
async def test_local_backend_continues_and_cancels_interrupted_a2a_task(tmp_path):
    settings = _settings(tmp_path)
    backend = LocalDevelopmentBackend(settings, _services(_evidence()))
    session_uid = settings.local_session_uid("continuation")
    bootstrap = await backend.bootstrap_tau_runtime(session_uid, _bootstrap("holder-1"))
    creation = await backend.create_task(
        {
            "task_id": "task-input",
            "context_id": session_uid,
            "initial_message": {
                "messageId": "message-1",
                "role": "ROLE_REQUESTER",
                "parts": [{"text": "Start."}],
            },
        }
    )
    dispatch = (await backend.list_task_dispatches(creation.task.uid))[0]
    attempt = await backend.claim_task_dispatch(
        creation.task.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        dispatch_uid=dispatch.uid,
    )
    attempt = await backend.start_task_attempt(
        creation.task.uid,
        attempt_uid=attempt.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        turn_uid="turn-task-input",
    )
    await backend.append_entries(
        session_uid,
        SessionEntryBatchAppendRequest(
            lease_token=bootstrap.lease.lease_token,
            holder_id="holder-1",
            expected_sequence=0,
            entries=[],
            turn=TauTurnLifecycle(
                turn_uid="turn-task-input",
                phase="committed",
                activity_sequence=1,
            ),
        ),
    )
    interrupted_attempt = await backend.settle_task_attempt(
        creation.task.uid,
        attempt_uid=attempt.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        status="input_required",
        status_message={
            "messageId": "status-message-1",
            "contextId": session_uid,
            "taskId": "task-input",
            "role": "ROLE_RESPONDER",
            "parts": [{"text": "Choose a value."}],
            "extensions": [],
        },
    )
    interrupted = await backend.get_task(creation.task.uid)
    continued = await backend.continue_task(
        creation.task.uid,
        {
            "messageId": "message-2",
            "contextId": session_uid,
            "taskId": "task-input",
            "role": "ROLE_REQUESTER",
            "parts": [{"text": "Use 42."}],
        },
    )
    continued = await backend.update_task_recovery_metadata(
        creation.task.uid,
        output_contract={"mode": "json", "schema": None, "repair_attempts": 2},
        provenance={"channel": "a2a", "origin": "agent"},
    )
    canceled = await backend.cancel_task(creation.task.uid)
    history = await backend.get_task(creation.task.uid, history_length=100)
    listed = await backend.list_tasks(context_id=session_uid, history_length=2)
    events = await backend.list_task_events(
        creation.task.uid,
        after_sequence=0,
        limit=100,
    )

    assert interrupted_attempt.state == "interrupted"
    assert interrupted.status == "input_required"
    assert continued.status == "submitted"
    assert continued.latest_message is not None
    assert continued.latest_message["messageId"] == "message-2"
    assert continued.metadata["execution"]["output_contract"]["mode"] == "json"
    assert canceled.status == "canceled"
    assert canceled.cancellation_requested is True
    assert [message["messageId"] for message in history.history or []] == [
        "message-1",
        "status-message-1",
        "message-2",
    ]
    assert [message["messageId"] for message in listed[0].history or []] == [
        "status-message-1",
        "message-2",
    ]
    status_event = next(event for event in events.events if event.status == "input_required")
    assert status_event.message_uid == "status-message-1"
    assert status_event.payload == {"messageId": "status-message-1"}

    await backend.aclose()


@pytest.mark.asyncio
async def test_local_task_list_batches_history_and_zero_skips_the_tail_read(
    tmp_path,
    monkeypatch,
):
    settings = _settings(tmp_path)
    backend = LocalDevelopmentBackend(settings, _services(_evidence()))
    for number in (1, 2):
        await backend.create_task(
            {
                "task_id": f"task-{number}",
                "context_id": f"context-{number}",
                "initial_message": {
                    "messageId": f"message-{number}",
                    "role": "ROLE_REQUESTER",
                    "parts": [{"text": f"Request {number}."}],
                    "extensions": [],
                },
            }
        )

    original = backend._task_message_tails
    history_reads = Mock(wraps=original)
    monkeypatch.setattr(backend, "_task_message_tails", history_reads)

    without_history = await backend.list_tasks(history_length=0)
    with_history = await backend.list_tasks(history_length=1)

    assert all(task.history == [] for task in without_history)
    assert history_reads.call_count == 1
    assert [task.history[0]["messageId"] for task in with_history if task.history] == [
        "message-1",
        "message-2",
    ]
    await backend.aclose()


@pytest.mark.asyncio
async def test_local_failed_settlement_abandons_turn_and_replays_exactly(tmp_path):
    settings = _settings(tmp_path)
    backend = LocalDevelopmentBackend(settings, _services(_evidence()))
    session_uid = settings.local_session_uid("failed-turn")
    bootstrap = await backend.bootstrap_tau_runtime(session_uid, _bootstrap("holder-1"))
    creation = await backend.create_task(
        {
            "task_id": "task-failed-turn",
            "context_id": session_uid,
            "initial_message": {
                "messageId": "request-1",
                "role": "ROLE_REQUESTER",
                "parts": [{"text": "Run."}],
                "extensions": [],
            },
        }
    )
    dispatch = (await backend.list_task_dispatches(creation.task.uid))[0]
    attempt = await backend.claim_task_dispatch(
        creation.task.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        dispatch_uid=dispatch.uid,
    )
    started = await backend.start_task_attempt(
        creation.task.uid,
        attempt_uid=attempt.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        turn_uid="turn-failed-1",
    )
    await backend.append_entries(
        session_uid,
        SessionEntryBatchAppendRequest(
            lease_token=bootstrap.lease.lease_token,
            holder_id="holder-1",
            expected_sequence=0,
            entries=[
                SessionEntryBatchItem(
                    idempotency_key="failed-entry-1",
                    entry={"type": "custom", "phase": "before-failure"},
                )
            ],
            turn=TauTurnLifecycle(
                turn_uid="turn-failed-1",
                phase="progress",
                activity_sequence=1,
            ),
        ),
    )
    status_message = {
        "messageId": "failure-message-1",
        "contextId": session_uid,
        "taskId": "task-failed-turn",
        "role": "ROLE_RESPONDER",
        "parts": [{"text": "The Task failed."}],
        "extensions": [],
    }
    settled = await backend.settle_task_attempt(
        creation.task.uid,
        attempt_uid=attempt.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        status="failed",
        status_message=status_message,
        outcome_category="execution",
        failure_detail="ValueError",
    )
    replayed = await backend.settle_task_attempt(
        creation.task.uid,
        attempt_uid=attempt.uid,
        holder_id="holder-1",
        lease_token="already-cleared-lease-is-not-required-for-exact-replay",
        status="failed",
        status_message=status_message,
        outcome_category="execution",
        failure_detail="ValueError",
    )

    entries = await backend.get_entries(session_uid)
    history = await backend.list_task_messages(creation.task.uid, limit=100)
    events_before_conflict = await backend.list_task_events(
        creation.task.uid, after_sequence=0, limit=100
    )
    assert started.entry_start_sequence == 0
    assert settled.turn_uid == "turn-failed-1"
    assert settled.turn_resolution == "abandoned"
    assert settled.entry_start_sequence == 0
    assert settled.entry_end_sequence == 1
    assert replayed == settled
    assert entries.entries[0].turn_uid == "turn-failed-1"
    assert [message["messageId"] for message in history] == [
        "request-1",
        "failure-message-1",
    ]

    with pytest.raises(BackendConflictError, match="Resolved Task turn"):
        await backend.append_entries(
            session_uid,
            SessionEntryBatchAppendRequest(
                lease_token=bootstrap.lease.lease_token,
                holder_id="holder-1",
                expected_sequence=1,
                entries=[],
                turn=TauTurnLifecycle(
                    turn_uid="turn-failed-1",
                    phase="committed",
                    activity_sequence=2,
                ),
            ),
        )
    with pytest.raises(BackendConflictError, match="replay diverged"):
        await backend.settle_task_attempt(
            creation.task.uid,
            attempt_uid=attempt.uid,
            holder_id="holder-1",
            lease_token=bootstrap.lease.lease_token,
            status="failed",
            status_message={**status_message, "parts": [{"text": "Changed."}]},
            outcome_category="execution",
            failure_detail="ValueError",
        )
    events_after_conflict = await backend.list_task_events(
        creation.task.uid, after_sequence=0, limit=100
    )
    assert events_after_conflict == events_before_conflict
    await backend.aclose()


@pytest.mark.asyncio
async def test_local_cancel_abandons_a_running_task_turn_and_releases_lease(tmp_path):
    settings = _settings(tmp_path)
    backend = LocalDevelopmentBackend(settings, _services(_evidence()))
    session_uid = settings.local_session_uid("cancel-turn")
    bootstrap = await backend.bootstrap_tau_runtime(session_uid, _bootstrap("holder-1"))
    creation = await backend.create_task(
        {
            "task_id": "task-cancel-turn",
            "context_id": session_uid,
            "initial_message": {
                "messageId": "request-cancel-1",
                "role": "ROLE_REQUESTER",
                "parts": [{"text": "Cancel this."}],
                "extensions": [],
            },
        }
    )
    dispatch = (await backend.list_task_dispatches(creation.task.uid))[0]
    attempt = await backend.claim_task_dispatch(
        creation.task.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        dispatch_uid=dispatch.uid,
    )
    await backend.start_task_attempt(
        creation.task.uid,
        attempt_uid=attempt.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        turn_uid="turn-cancel-1",
    )
    await backend.append_entries(
        session_uid,
        SessionEntryBatchAppendRequest(
            lease_token=bootstrap.lease.lease_token,
            holder_id="holder-1",
            expected_sequence=0,
            entries=[
                SessionEntryBatchItem(
                    idempotency_key="cancel-entry-1",
                    entry={"type": "custom", "phase": "before-cancel"},
                )
            ],
            turn=TauTurnLifecycle(
                turn_uid="turn-cancel-1",
                phase="progress",
                activity_sequence=1,
            ),
        ),
    )

    canceled = await backend.cancel_task(creation.task.uid)
    runtime_state = await backend.get_runtime_state(session_uid)
    with sqlite3.connect(settings.local_state_path) as connection:
        attempt_state = connection.execute(
            "SELECT state, entry_start_sequence, entry_end_sequence, turn_resolution "
            "FROM a2a_task_attempts WHERE uid=?",
            (attempt.uid,),
        ).fetchone()

    assert canceled.status == "canceled"
    assert attempt_state == ("canceled", 0, 1, "abandoned")
    assert runtime_state.active_turn_uid is None
    assert runtime_state.active_task_attempt_uid is None
    await backend.release_runtime_lease(
        session_uid,
        RuntimeLeaseReleaseRequest(
            lease_token=bootstrap.lease.lease_token,
            holder_id="holder-1",
            reason="runtime_eviction",
        ),
    )
    await backend.aclose()


@pytest.mark.asyncio
async def test_local_reconciliation_returns_unclaimed_submitted_tasks(tmp_path):
    settings = _settings(tmp_path)
    backend = LocalDevelopmentBackend(settings, _services(_evidence()))
    session_uid = settings.local_session_uid("recover-submitted")
    created = await backend.create_task(
        {
            "task_id": "task-submitted",
            "context_id": session_uid,
            "initial_message": {
                "messageId": "message-1",
                "role": "ROLE_REQUESTER",
                "parts": [{"text": "Recover me."}],
            },
            "metadata": {
                "execution": {
                    "output_contract": {
                        "mode": "none",
                        "schema": None,
                        "repair_attempts": 3,
                    },
                    "provenance": {"channel": "a2a", "origin": "agent"},
                }
            },
        }
    )

    recoverable = await backend.reconcile_a2a_tasks(
        recovery_owner="runtime-2",
        stale_after_seconds=1,
        pending_timeout_seconds=300,
        max_recovery_attempts=3,
    )

    assert [task.uid for task in recoverable] == [created.task.uid]
    assert recoverable[0].latest_message["parts"] == [{"text": "Recover me."}]
    await backend.aclose()


@pytest.mark.asyncio
async def test_local_reconciliation_does_not_steal_submitted_task_from_live_session_owner(
    tmp_path,
):
    settings = _settings(tmp_path)
    backend = LocalDevelopmentBackend(settings, _services(_evidence()))
    session_uid = settings.local_session_uid("owned-elsewhere")
    await backend.bootstrap_tau_runtime(session_uid, _bootstrap("runtime-1"))
    created = await backend.create_task(
        {
            "task_id": "task-owned-elsewhere",
            "context_id": session_uid,
            "initial_message": {
                "messageId": "message-1",
                "role": "ROLE_REQUESTER",
                "parts": [{"text": "The current owner must run this."}],
            },
        }
    )

    recoverable = await backend.reconcile_a2a_tasks(
        recovery_owner="runtime-2",
        stale_after_seconds=1,
        pending_timeout_seconds=1,
        max_recovery_attempts=1,
    )
    unchanged = await backend.get_task(created.task.uid)

    assert recoverable == []
    assert unchanged.status == "submitted"
    assert unchanged.recovery_count == 0
    await backend.aclose()


@pytest.mark.asyncio
async def test_local_reconciler_claims_safe_submitted_task_for_execution(tmp_path):
    settings = _settings(tmp_path)
    backend = LocalDevelopmentBackend(settings, _services(_evidence()))
    session_uid = settings.local_session_uid("recover-and-run")
    bootstrap = await backend.bootstrap_tau_runtime(session_uid, _bootstrap("runtime-2"))
    created = await backend.create_task(
        {
            "task_id": "task-recovered",
            "context_id": session_uid,
            "initial_message": {
                "messageId": "message-1",
                "role": "ROLE_REQUESTER",
                "parts": [{"text": "Recover and execute."}],
            },
            "metadata": {
                "execution": {
                    "output_contract": {
                        "mode": "none",
                        "schema": None,
                        "repair_attempts": 3,
                    },
                    "provenance": {"channel": "a2a", "origin": "agent"},
                }
            },
        }
    )

    class Manager:
        holder_id = "runtime-2"

        async def get(self, requested_session_uid):
            assert requested_session_uid == session_uid
            return object()

        async def task_execution_fence(self, requested_session_uid):
            assert requested_session_uid == session_uid
            return SimpleNamespace(
                holder_id="runtime-2",
                lease_token=bootstrap.lease.lease_token,
            )

        def create_a2a_task_execution(self, task_uid, coroutine, *, name):
            assert task_uid == created.task.uid
            assert name == "a2a-task-task-recovered"
            coroutine.close()
            return Mock(), True

    await _reconcile_local_tasks_once(backend, Manager(), settings)  # type: ignore[arg-type]
    claimed = await backend.get_task(created.task.uid)

    assert claimed.status == "working"
    assert claimed.recovery_count == 0
    await backend.aclose()


@pytest.mark.asyncio
async def test_local_reconciliation_terminalizes_exhausted_submitted_task(tmp_path):
    settings = _settings(tmp_path)
    backend = LocalDevelopmentBackend(settings, _services(_evidence()))
    created = await backend.create_task(
        {
            "task_id": "task-exhausted",
            "context_id": settings.local_session_uid("exhausted"),
            "initial_message": {
                "messageId": "message-1",
                "role": "ROLE_REQUESTER",
                "parts": [{"text": "Never starts."}],
            },
        }
    )
    for _ in range(3):
        await backend.record_a2a_recovery_failure(
            created.task.uid,
            recovery_owner="runtime-2",
            error_type="ProviderUnavailable",
        )

    recoverable = await backend.reconcile_a2a_tasks(
        recovery_owner="runtime-2",
        stale_after_seconds=1,
        pending_timeout_seconds=300,
        max_recovery_attempts=3,
    )
    failed = await backend.get_task(created.task.uid)

    assert recoverable == []
    assert failed.status == "failed"
    assert failed.failure_code == "recovery_exhausted"
    assert failed.failure_category == "recovery"
    assert failed.failure_retryable is False
    assert failed.status_message["role"] == "ROLE_RESPONDER"
    assert failed.status_message["metadata"][TASK_FAILURE_EXTENSION_URI]["code"] == (
        "recovery_exhausted"
    )
    await backend.aclose()


@pytest.mark.asyncio
async def test_local_reconciliation_fails_stale_working_task_as_ambiguous(tmp_path):
    settings = _settings(tmp_path)
    backend = LocalDevelopmentBackend(settings, _services(_evidence()))
    session_uid = settings.local_session_uid("recover-working")
    bootstrap = await backend.bootstrap_tau_runtime(session_uid, _bootstrap("runtime-1"))
    created = await backend.create_task(
        {
            "task_id": "task-working",
            "context_id": session_uid,
            "initial_message": {
                "messageId": "message-1",
                "role": "ROLE_REQUESTER",
                "parts": [{"text": "May have side effects."}],
            },
        }
    )
    dispatch = (await backend.list_task_dispatches(created.task.uid))[0]
    attempt = await backend.claim_task_dispatch(
        created.task.uid,
        holder_id="runtime-1",
        lease_token=bootstrap.lease.lease_token,
        dispatch_uid=dispatch.uid,
    )
    await backend.start_task_attempt(
        created.task.uid,
        attempt_uid=attempt.uid,
        holder_id="runtime-1",
        lease_token=bootstrap.lease.lease_token,
        turn_uid="turn-task-working",
    )
    old = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    with sqlite3.connect(settings.local_state_path) as connection:
        connection.execute(
            "UPDATE leases SET expires_at = ? WHERE session_uid = ?",
            (old, session_uid),
        )
        connection.execute(
            "UPDATE a2a_task_attempts SET updated_at = ? WHERE uid = ?",
            (old, attempt.uid),
        )

    with pytest.raises(BackendConflictError, match="Task turn recovery is pending"):
        await backend.bootstrap_tau_runtime(session_uid, _bootstrap("runtime-2"))

    recoverable = await backend.reconcile_a2a_tasks(
        recovery_owner="runtime-2",
        stale_after_seconds=1,
        pending_timeout_seconds=300,
        max_recovery_attempts=3,
    )
    failed = await backend.get_task(created.task.uid)
    with sqlite3.connect(settings.local_state_path) as connection:
        attempt_state = connection.execute(
            "SELECT state, failure_code FROM a2a_task_attempts WHERE uid = ?",
            (attempt.uid,),
        ).fetchone()

    assert recoverable == []
    assert failed.status == "failed"
    assert failed.failure_code == "ambiguous_execution_outcome"
    assert failed.failure_category == "ambiguous_outcome"
    assert failed.recovery_owner == "runtime-2"
    assert attempt_state == ("ambiguous", "ambiguous_execution_outcome")
    assert backend.a2a_recovery_snapshot()["ambiguous_outcomes"] == 1
    resumed = await backend.bootstrap_tau_runtime(session_uid, _bootstrap("runtime-2"))
    assert resumed.lease.holder_id == "runtime-2"
    assert resumed.runtime_state.active_task_attempt_uid is None
    await backend.aclose()
