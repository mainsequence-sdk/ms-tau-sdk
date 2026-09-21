import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

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
from ms_tau_sdk.errors import LocalModeUnsupportedError
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
            "message_id": "message-1",
            "role": "user",
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
    assert completed.latest_message["message_id"] == "message-1"
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
                "message_id": "message-1",
                "role": "user",
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
    )
    interrupted_attempt = await backend.settle_task_attempt(
        creation.task.uid,
        attempt_uid=attempt.uid,
        holder_id="holder-1",
        lease_token=bootstrap.lease.lease_token,
        status="input_required",
        status_message={"message": "Choose a value."},
    )
    interrupted = await backend.get_task(creation.task.uid)
    continued = await backend.continue_task(
        creation.task.uid,
        {
            "message_id": "message-2",
            "role": "user",
            "parts": [{"text": "Use 42."}],
        },
    )
    canceled = await backend.cancel_task(creation.task.uid)

    assert interrupted_attempt.state == "interrupted"
    assert interrupted.status == "input_required"
    assert continued.status == "submitted"
    assert continued.latest_message is not None
    assert continued.latest_message["message_id"] == "message-2"
    assert canceled.status == "canceled"
    assert canceled.cancellation_requested is True

    await backend.aclose()
