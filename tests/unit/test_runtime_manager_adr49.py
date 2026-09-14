import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from tau_agent.session import SessionInfoEntry
from tau_agent.tools import AgentTool
from tau_coding.resources import ResourceDiagnostic

from astro.backend.models import (
    AgentSession,
    ProviderControl,
    ProviderCredential,
    RuntimeLease,
    RuntimeState,
    SessionEntryList,
    SessionEntryRecord,
    TauResumeSnapshot,
    TauResumeSnapshotUploadResponse,
    TauRuntimeBootstrap,
    TauTurnCommit,
)
from astro.errors import BackendConflictError
from astro.runtime.manager import ADR49_RUNTIME_CAPABILITIES, SessionRuntimeManager
from astro.runtime.session import ActiveSessionRuntime
from astro.runtime.snapshots import build_snapshot_upload
from astro.sessions.storage import SESSION_ENTRY_ADAPTER
from astro.settings import Settings


def _settings(tmp_path):
    return Settings(
        _env_file=None,
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
        code_repository_root=tmp_path,
        startup_dependencies_enabled=False,
    )


def _entry_record(entry, sequence):
    payload = SESSION_ENTRY_ADAPTER.dump_python(entry, mode="json", exclude_none=True)
    return SessionEntryRecord(
        sequence=sequence,
        entry_type=payload["type"],
        entry_json=payload,
        idempotency_key=entry.id,
    )


def _bootstrap(
    session_uid,
    *,
    history=None,
    snapshot=None,
    runtime_config_sha256="sha256:runtime",
    capability_set_sha256="sha256:capabilities",
):
    return TauRuntimeBootstrap(
        session=AgentSession(
            uid=session_uid,
            agent_uid="agent-1",
            harness="tau",
            harness_protocol="tau-session-v1",
            harness_version="1",
            active_provider="test-provider",
            active_model="test-model",
            runtime_config_sha256=runtime_config_sha256,
            capability_set_sha256=capability_set_sha256,
        ),
        lease=RuntimeLease(
            lease_token=f"lease-{session_uid}",
            holder_id="holder",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ),
        runtime_state=RuntimeState(
            harness="tau",
            harness_protocol="tau-session-v1",
            harness_version="1",
            runtime_activity="loading",
            activity_revision=1,
            activity_sequence=0,
            runtime_capabilities=ADR49_RUNTIME_CAPABILITIES,
        ),
        history=history or SessionEntryList(entries=[], next_sequence=0),
        resume_snapshot=snapshot,
        capabilities=[],
        provider_credentials={},
        provider_control=ProviderControl(
            schema_version=1,
            catalog_digest=f"sha256:{'0' * 64}",
            provider="test-provider",
            model={
                "model": "test-model",
                "api": "openai-completions",
                "input": ["text"],
                "reasoning": False,
                "thinking_levels": [],
            },
        ),
        runtime_capabilities=ADR49_RUNTIME_CAPABILITIES,
    )


def _manager_dependencies(tmp_path, bootstraps):
    backend = AsyncMock()
    backend.auth = Mock()
    backend.bootstrap_tau_runtime.side_effect = list(bootstraps)
    backend.provider_credential_from_hydration = Mock(
        return_value=ProviderCredential(
            provider="test-provider",
            api_key="secret",
        )
    )
    backend.patch_runtime_activity.return_value = RuntimeState(
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="1",
        runtime_activity="idle",
        activity_revision=2,
        activity_sequence=1,
        applied=True,
        runtime_capabilities=ADR49_RUNTIME_CAPABILITIES,
    )
    providers = Mock()
    provider_runtime = SimpleNamespace(
        provider=object(),
        name="test-provider",
        model="test-model",
        thinking_level=None,
    )
    providers.for_session_credential.return_value = provider_runtime
    manager = SessionRuntimeManager(
        settings=_settings(tmp_path),
        backend=backend,
        providers=providers,
    )
    return manager, backend, providers


def _mcp_client():
    return SimpleNamespace(tools=(), resources=(), aclose=AsyncMock())


def _coding_session(
    *,
    tools=(),
    extension_names=(),
    extension_tool_sources=None,
):
    session = Mock(is_running=False)
    session.tools = tools
    session.extension_names = extension_names
    session.extension_tool_sources = extension_tool_sources or {}
    session.extension_runtime = SimpleNamespace(diagnostics=())
    return session


@pytest.mark.asyncio
async def test_stale_pre_provider_lease_reloads_once_before_execution(tmp_path):
    manager, _backend, _providers = _manager_dependencies(tmp_path, [])
    stale_storage = SimpleNamespace(
        begin_turn=AsyncMock(side_effect=BackendConflictError("stale lease")),
        invalidate_lease=Mock(),
    )
    fresh_storage = SimpleNamespace(
        begin_turn=AsyncMock(
            return_value=RuntimeState(
                harness="tau",
                harness_protocol="tau-session-v1",
                harness_version="1",
                runtime_activity="working",
                active_turn_uid="turn-1",
                activity_sequence=1,
            )
        )
    )
    stale = SimpleNamespace(storage=stale_storage, activity_sequence=0)
    fresh = SimpleNamespace(
        storage=fresh_storage,
        activity_sequence=0,
        runtime_activity="idle",
        active_turn_uid=None,
    )
    manager.evict = AsyncMock()
    manager.get = AsyncMock(return_value=fresh)

    selected = await manager._begin_turn_with_one_reload(
        session_uid="session-1",
        runtime=stale,
        turn_uid="turn-1",
    )

    assert selected is fresh
    stale_storage.invalidate_lease.assert_called_once_with()
    manager.evict.assert_awaited_once_with("session-1")
    manager.get.assert_awaited_once_with("session-1")
    fresh_storage.begin_turn.assert_awaited_once_with(
        turn_uid="turn-1",
        activity_sequence=1,
    )
    await manager.aclose()


@pytest.mark.asyncio
async def test_v1_cold_load_uses_one_bootstrap_and_reuses_process_mcp(tmp_path):
    manager, backend, providers = _manager_dependencies(
        tmp_path,
        [_bootstrap("session-1"), _bootstrap("session-2")],
    )
    mcp_client = _mcp_client()
    coding_sessions = [_coding_session(), _coding_session()]

    with (
        patch(
            "astro.runtime.manager.materialize_bootstrap_capabilities",
            AsyncMock(return_value=tmp_path / ".agents"),
        ) as materialize,
        patch(
            "astro.runtime.manager.MainSequenceMCPClient.connect",
            AsyncMock(return_value=mcp_client),
        ) as connect,
        patch(
            "astro.runtime.manager.create_mainsequence_mcp_tools",
            return_value=[],
        ) as create_mcp_tools,
        patch("astro.runtime.manager.create_coding_tools", return_value=[]),
        patch("astro.runtime.manager.create_file_tools", return_value=[]),
        patch("astro.runtime.manager.build_web_tools", return_value=[]),
        patch(
            "astro.runtime.manager.CodingSession.load",
            AsyncMock(side_effect=coding_sessions),
        ) as load_coding_session,
    ):
        first = await manager.get("session-1")
        same = await manager.get("session-1")
        second = await manager.get("session-2")
        await asyncio.sleep(0)

    assert same is first
    assert second is not first
    assert backend.bootstrap_tau_runtime.await_count == 2
    backend.get_session.assert_not_awaited()
    backend.acquire_runtime_lease.assert_not_awaited()
    backend.get_entries.assert_not_awaited()
    backend.hydrate_provider_credential.assert_not_awaited()
    assert providers.for_session_credential.call_count == 2
    assert materialize.await_count == 2
    connect.assert_awaited_once_with(settings=manager.settings, auth=backend.auth)
    for load_call in load_coding_session.await_args_list:
        assert [tool.name for tool in load_call.args[0].tools] == ["runtime_info"]
        assert load_call.args[0].project_extensions_enabled is False
    assert create_mcp_tools.call_args_list == [
        (
            (mcp_client,),
            {
                "caller_session_proof": {
                    "caller_agent_session_uid": session_uid,
                    "lease_holder_id": "holder",
                    "lease_token": f"lease-{session_uid}",
                }
            },
        )
        for session_uid in ("session-1", "session-2")
    ]
    assert first.storage.next_sequence == 0
    assert second.storage.next_sequence == 0

    await manager.aclose()
    mcp_client.aclose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_executor_setting_enables_tau_extensions_and_reports_effective_catalog(tmp_path):
    manager, _backend, _providers = _manager_dependencies(
        tmp_path,
        [_bootstrap("session-1")],
    )
    manager.settings = manager.settings.model_copy(
        update={"code_repository_extensions_enabled": True}
    )
    project_tool = AgentTool(
        name="project_tool",
        label="Project Tool",
        description="A repository extension tool.",
        parameters={"type": "object", "properties": {}},
        execute_fn=AsyncMock(),
    )
    loaded_session = _coding_session(
        extension_names=("project_extension",),
        extension_tool_sources={"project_tool": "project_extension"},
    )

    async def load_with_project_tool(config):
        loaded_session.tools = (*config.tools, project_tool)
        return loaded_session

    with (
        patch(
            "astro.runtime.manager.materialize_bootstrap_capabilities",
            AsyncMock(return_value=tmp_path / ".agents"),
        ),
        patch(
            "astro.runtime.manager.MainSequenceMCPClient.connect",
            AsyncMock(return_value=_mcp_client()),
        ),
        patch("astro.runtime.manager.create_mainsequence_mcp_tools", return_value=[]),
        patch("astro.runtime.manager.create_coding_tools", return_value=[]),
        patch("astro.runtime.manager.create_file_tools", return_value=[]),
        patch("astro.runtime.manager.build_web_tools", return_value=[]),
        patch(
            "astro.runtime.manager.CodingSession.load",
            AsyncMock(side_effect=load_with_project_tool),
        ) as load_coding_session,
    ):
        runtime = await manager.get("session-1")

    config = load_coding_session.await_args.args[0]
    assert config.project_extensions_enabled is True
    assert runtime.project_extension_state is not None
    assert runtime.project_extension_state.loaded_extension_count == 1
    assert runtime.project_extension_state.project_tool_count == 1
    assert runtime.project_extension_state.extension_diagnostic_count == 0
    assert runtime.project_extension_state.extension_error_count == 0
    assert runtime.project_extension_state.tool_catalog_digest.startswith("sha256:")

    runtime_info = config.tools[-1]
    payload = json.loads((await runtime_info.execute("call-1", {})).text)
    assert payload["code_repository_extensions_enabled"] is True
    assert payload["loaded_extension_count"] == 1
    assert payload["project_tool_count"] == 1
    assert payload["extension_diagnostic_count"] == 0
    assert payload["extension_error_count"] == 0
    assert payload["tool_catalog_digest"] == runtime.project_extension_state.tool_catalog_digest

    snapshot = manager.snapshot()
    assert snapshot["code_repository_extensions_enabled"] is True
    assert snapshot["loaded_extension_count"] == 1
    assert snapshot["project_tool_count"] == 1
    assert snapshot["extension_diagnostic_count"] == 0
    assert snapshot["extension_error_count"] == 0
    assert snapshot["tool_catalog_digest"] == runtime.project_extension_state.tool_catalog_digest

    await manager.aclose()


def test_extension_diagnostics_are_structured_and_repository_relative(tmp_path):
    state = SimpleNamespace(
        enabled=True,
        loaded_extension_count=0,
        project_tool_count=0,
        extension_diagnostic_count=1,
        extension_error_count=1,
        tool_catalog_digest=f"sha256:{'a' * 64}",
    )
    coding_session = SimpleNamespace(
        extension_runtime=SimpleNamespace(
            diagnostics=(
                ResourceDiagnostic(
                    kind="extension",
                    name="broken_extension",
                    path=tmp_path / ".tau/extensions/broken_extension/extension.py",
                    message="failed to import extension: RuntimeError('sensitive detail')",
                    severity="error",
                ),
            )
        )
    )

    with patch("astro.runtime.manager.logger") as structured_logger:
        SessionRuntimeManager._log_project_extension_diagnostics(
            session_uid="session-1",
            cwd=tmp_path,
            coding_session=coding_session,
            state=state,
        )

    structured_logger.info.assert_called_once()
    structured_logger.error.assert_called_once()
    diagnostic = structured_logger.error.call_args.kwargs
    assert diagnostic == {
        "session_uid": "session-1",
        "extension_name": "broken_extension",
        "extension_path": ".tau/extensions/broken_extension/extension.py",
        "diagnostic_kind": "extension",
        "diagnostic_severity": "error",
        "error_type": "import_error",
    }


@pytest.mark.asyncio
async def test_v1_restores_compatible_snapshot_and_applies_only_delta(tmp_path):
    first = SessionInfoEntry(cwd="/snapshot")
    second = SessionInfoEntry(cwd="/delta")
    upload = build_snapshot_upload(
        holder_id="holder",
        lease_token="lease",
        entries=[first],
        base_sequence=1,
        last_committed_turn_uid="turn-1",
        runtime_config_sha256="sha256:runtime",
        capability_set_sha256="sha256:capabilities",
    )
    snapshot = TauResumeSnapshot(
        **upload.model_dump(exclude={"holder_id", "lease_token"}),
        canonical_size=1,
    )
    history = SessionEntryList(
        entries=[_entry_record(second, 1)],
        next_sequence=2,
    )
    manager, backend, _providers = _manager_dependencies(
        tmp_path,
        [_bootstrap("session-1", history=history, snapshot=snapshot)],
    )

    with (
        patch(
            "astro.runtime.manager.materialize_bootstrap_capabilities",
            AsyncMock(return_value=tmp_path / ".agents"),
        ),
        patch(
            "astro.runtime.manager.MainSequenceMCPClient.connect",
            AsyncMock(return_value=_mcp_client()),
        ),
        patch("astro.runtime.manager.create_mainsequence_mcp_tools", return_value=[]),
        patch("astro.runtime.manager.create_coding_tools", return_value=[]),
        patch("astro.runtime.manager.create_file_tools", return_value=[]),
        patch("astro.runtime.manager.build_web_tools", return_value=[]),
        patch(
            "astro.runtime.manager.CodingSession.load",
            AsyncMock(return_value=_coding_session()),
        ),
    ):
        runtime = await manager.get("session-1")

    restored = await runtime.storage.read_all()
    assert [entry.id for entry in restored] == [first.id, second.id]
    assert runtime.storage.next_sequence == 2
    backend.get_entries.assert_not_awaited()
    await manager.aclose()


@pytest.mark.asyncio
async def test_v1_corrupt_snapshot_falls_back_to_canonical_history(tmp_path):
    durable_entry = SessionInfoEntry(cwd="/durable")
    upload = build_snapshot_upload(
        holder_id="holder",
        lease_token="lease",
        entries=[durable_entry],
        base_sequence=1,
        last_committed_turn_uid="turn-1",
        runtime_config_sha256="sha256:runtime",
        capability_set_sha256="sha256:capabilities",
    )
    snapshot_payload = upload.model_dump(exclude={"holder_id", "lease_token"})
    snapshot_payload["payload_sha256"] = "sha256:corrupt"
    snapshot = TauResumeSnapshot(**snapshot_payload, canonical_size=1)
    durable = SessionEntryList(
        entries=[_entry_record(durable_entry, 0)],
        next_sequence=1,
    )
    manager, backend, _providers = _manager_dependencies(
        tmp_path,
        [_bootstrap("session-1", snapshot=snapshot)],
    )
    backend.get_entries.return_value = durable

    with (
        patch(
            "astro.runtime.manager.materialize_bootstrap_capabilities",
            AsyncMock(return_value=tmp_path / ".agents"),
        ),
        patch(
            "astro.runtime.manager.MainSequenceMCPClient.connect",
            AsyncMock(return_value=_mcp_client()),
        ),
        patch("astro.runtime.manager.create_mainsequence_mcp_tools", return_value=[]),
        patch("astro.runtime.manager.create_coding_tools", return_value=[]),
        patch("astro.runtime.manager.create_file_tools", return_value=[]),
        patch("astro.runtime.manager.build_web_tools", return_value=[]),
        patch(
            "astro.runtime.manager.CodingSession.load",
            AsyncMock(return_value=_coding_session()),
        ),
    ):
        runtime = await manager.get("session-1")

    assert [entry.id for entry in await runtime.storage.read_all()] == [durable_entry.id]
    backend.get_entries.assert_awaited_once_with("session-1")
    await manager.aclose()


@pytest.mark.asyncio
async def test_startup_readiness_waits_for_auth_and_shared_mcp(tmp_path):
    backend = AsyncMock()
    auth_started = asyncio.Event()
    release_auth = asyncio.Event()
    mcp_started = asyncio.Event()
    release_mcp = asyncio.Event()

    async def prefetch():
        auth_started.set()
        await release_auth.wait()

    async def connect(**_kwargs):
        mcp_started.set()
        await release_mcp.wait()
        return _mcp_client()

    backend.auth.prefetch.side_effect = prefetch
    manager = SessionRuntimeManager(
        settings=_settings(tmp_path).model_copy(update={"startup_dependencies_enabled": True}),
        backend=backend,
        providers=Mock(),
    )

    with patch(
        "astro.runtime.manager.MainSequenceMCPClient.connect",
        AsyncMock(side_effect=connect),
    ):
        start_task = asyncio.create_task(manager.start())
        await auth_started.wait()
        assert manager.snapshot()["startup_ready"] is False
        release_auth.set()
        await mcp_started.wait()
        assert manager.snapshot()["startup_ready"] is False
        release_mcp.set()
        await start_task

    assert manager.snapshot()["startup_ready"] is True
    await manager.aclose()


@pytest.mark.asyncio
async def test_v1_turn_commit_blocks_done_but_snapshot_upload_does_not(tmp_path):
    class SettlingCodingSession:
        is_running = False

        async def prompt(self, _content):
            yield {"type": "text_delta", "delta": "ok"}
            yield {"type": "agent_settled"}

        def cancel(self):
            return None

    manager = SessionRuntimeManager(
        settings=_settings(tmp_path),
        backend=AsyncMock(),
        providers=Mock(),
    )
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()
    snapshot_started = asyncio.Event()
    release_snapshot = asyncio.Event()

    async def commit_turn(**_kwargs):
        commit_started.set()
        await release_commit.wait()
        return TauTurnCommit(
            turn_uid=_kwargs["turn_uid"],
            next_sequence=0,
            committed_at=datetime.now(UTC),
        )

    async def upload_snapshot(_session_uid, request):
        snapshot_started.set()
        await release_snapshot.wait()
        return TauResumeSnapshotUploadResponse(
            applied=True,
            replayed=False,
            base_sequence=request.base_sequence,
            last_committed_turn_uid=request.last_committed_turn_uid,
            snapshot_schema_version=request.snapshot_schema_version,
            tau_runtime_version=request.tau_runtime_version,
            runtime_config_sha256=request.runtime_config_sha256,
            capability_set_sha256=request.capability_set_sha256,
            payload_sha256=request.payload_sha256,
            canonical_size=1,
        )

    storage = SimpleNamespace(
        lease_token="lease",
        begin_turn=AsyncMock(),
        commit_turn=AsyncMock(side_effect=commit_turn),
        entries_at_sequence=AsyncMock(return_value=[]),
        flush=AsyncMock(),
        invalidate_lease=Mock(),
    )
    manager.backend.upload_tau_resume_snapshot.side_effect = upload_snapshot
    manager._runtimes["session-1"] = ActiveSessionRuntime(
        session_uid="session-1",
        holder_id=manager.holder_id,
        coding_session=SettlingCodingSession(),
        storage=storage,
        provider=object(),
        provider_name="test-provider",
        model="test-model",
        runtime_config_sha256="sha256:runtime",
        capability_set_sha256="sha256:capabilities",
    )
    received = []

    async def consume():
        async for event in manager.prompt("session-1", "hello"):
            received.append(event)

    consume_task = asyncio.create_task(consume())
    await commit_started.wait()
    assert [event.type for event in received] == [
        "lifecycle",
        "lifecycle",
        "text_delta",
        "agent_settled",
        "lifecycle",
    ]
    assert not consume_task.done()
    manager.backend.patch_runtime_activity.assert_not_awaited()

    release_commit.set()
    await consume_task
    assert received[-2].data["phase"] == "durable"
    assert received[-1].type == "persistence_settled"
    assert consume_task.done()
    assert not snapshot_started.is_set()

    assert manager.mark_response_delivered("session-1") is True
    await snapshot_started.wait()
    assert manager.mark_response_delivered("session-1") is False

    release_snapshot.set()
    manager._runtimes.clear()
    await manager.aclose()
