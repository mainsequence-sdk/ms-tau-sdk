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
