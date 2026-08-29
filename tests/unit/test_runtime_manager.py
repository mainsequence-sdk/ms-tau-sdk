import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from structlog.contextvars import bind_contextvars, clear_contextvars, get_contextvars

from astro.backend.models import AgentSession, RuntimeLease, SessionEntryList
from astro.errors import ConfigurationError
from astro.logging import configure_logging
from astro.runtime.manager import SessionRuntimeManager
from astro.settings import Settings


class _FakeCodingSession:
    def __init__(self, *, delay: float = 0.02) -> None:
        self.delay = delay
        self.is_running = False
        self.active = 0
        self.max_active = 0
        self.cancelled = False

    async def prompt(self, content: str):
        self.is_running = True
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            yield {"type": "text_delta", "delta": content}
        finally:
            self.active -= 1
            self.is_running = False

    def cancel(self) -> None:
        self.cancelled = True


def _loaded_manager(tmp_path, coding_session, *, timeout: float = 1):
    from astro.runtime.session import ActiveSessionRuntime

    backend = AsyncMock()
    settings = Settings(
        _env_file=None,
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
        code_repository_root=tmp_path,
        tau_runtime_contract="adr48",
    )
    settings.turn_timeout_seconds = timeout
    manager = SessionRuntimeManager(
        settings=settings,
        backend=backend,
        providers=Mock(),
    )
    manager._runtimes["session-1"] = ActiveSessionRuntime(
        session_uid="session-1",
        holder_id="test",
        coding_session=coding_session,
        storage=SimpleNamespace(
            lease_token="lease",
            flush=AsyncMock(),
            invalidate_lease=Mock(),
        ),
        provider=object(),
    )
    return manager, backend


@pytest.mark.asyncio
async def test_runtime_manager_rejects_pi_sessions(tmp_path):
    backend = AsyncMock()
    backend.get_session.return_value = AgentSession(
        uid="pi-session",
        harness="pi",
        harness_protocol="pi-checkpoint-v1",
        harness_version="0.52.12",
    )
    manager = SessionRuntimeManager(
        settings=Settings(
            _env_file=None,
            runtime_credential_id="credential-id",
            runtime_credential_secret="credential-secret",
            code_repository_root=tmp_path,
            tau_runtime_contract="adr48",
        ),
        backend=backend,
        providers=Mock(),
    )

    with pytest.raises(ConfigurationError, match="uses harness 'pi'"):
        await manager.get("pi-session")

    backend.acquire_runtime_lease.assert_not_awaited()
    await manager.aclose()


@pytest.mark.asyncio
async def test_runtime_manager_serializes_same_session_prompts(tmp_path):
    coding_session = _FakeCodingSession()
    manager, _backend = _loaded_manager(tmp_path, coding_session)

    async def consume(prompt):
        return [event async for event in manager.prompt("session-1", prompt)]

    first, second = await asyncio.gather(consume("first"), consume("second"))

    assert first[2].data["delta"] == "first"
    assert second[2].data["delta"] == "second"
    assert coding_session.max_active == 1
    manager._runtimes.clear()
    await manager.aclose()


@pytest.mark.asyncio
async def test_runtime_manager_emits_output_finish_before_persistence_settles(tmp_path):
    class SettlingCodingSession(_FakeCodingSession):
        async def prompt(self, content: str):
            yield {"type": "text_delta", "delta": content}
            yield {"type": "agent_settled"}

    manager, _backend = _loaded_manager(tmp_path, SettlingCodingSession())
    runtime = manager._runtimes["session-1"]
    flush_started = asyncio.Event()
    release_flush = asyncio.Event()
    first_event_received = asyncio.Event()
    received = []

    async def flush():
        flush_started.set()
        await release_flush.wait()

    async def consume():
        async for event in manager.prompt("session-1", "hello"):
            received.append(event)
            first_event_received.set()

    runtime.storage.flush = AsyncMock(side_effect=flush)
    consume_task = asyncio.create_task(consume())
    await first_event_received.wait()
    await flush_started.wait()

    assert [event.type for event in received] == [
        "lifecycle",
        "lifecycle",
        "text_delta",
        "agent_settled",
        "lifecycle",
    ]

    release_flush.set()
    await consume_task
    assert [event.type for event in received] == [
        "lifecycle",
        "lifecycle",
        "text_delta",
        "agent_settled",
        "lifecycle",
        "lifecycle",
        "persistence_settled",
    ]
    activities = [
        call.args[1].runtime_activity for call in _backend.patch_runtime_activity.await_args_list
    ]
    assert activities == ["working", "persisting", "idle"]

    manager._runtimes.clear()
    await manager.aclose()


@pytest.mark.asyncio
async def test_disconnect_after_finish_keeps_durability_task_running(tmp_path):
    class SettlingCodingSession(_FakeCodingSession):
        async def prompt(self, content: str):
            yield {"type": "text_delta", "delta": content}
            yield {"type": "agent_settled"}

    manager, _backend = _loaded_manager(tmp_path, SettlingCodingSession())
    runtime = manager._runtimes["session-1"]
    flush_started = asyncio.Event()
    release_flush = asyncio.Event()

    async def flush():
        flush_started.set()
        await release_flush.wait()

    runtime.storage.flush = AsyncMock(side_effect=flush)
    stream = manager.prompt("session-1", "hello")

    assert (await anext(stream)).data["phase"] == "loading_session"
    assert (await anext(stream)).data["phase"] == "generating"
    assert (await anext(stream)).type == "text_delta"
    assert (await anext(stream)).type == "agent_settled"
    await flush_started.wait()
    await stream.aclose()

    assert runtime.persistence_task is not None
    assert not runtime.persistence_task.done()
    release_flush.set()
    await runtime.persistence_task

    activities = [
        call.args[1].runtime_activity for call in _backend.patch_runtime_activity.await_args_list
    ]
    assert activities == ["working", "persisting", "idle"]
    manager._runtimes.clear()
    await manager.aclose()


@pytest.mark.asyncio
async def test_runtime_manager_logs_only_prompt_size_when_payload_logging_enabled(
    tmp_path,
    capsys,
):
    configure_logging("INFO", machine_sink=True, human_sink=False)
    manager, _backend = _loaded_manager(tmp_path, _FakeCodingSession())
    manager.settings.log_payloads = True

    _ = [
        event
        async for event in manager.prompt(
            "session-1",
            "  Analyze this\nportfolio allocation.  ",
        )
    ]
    manager._runtimes.clear()
    await manager.aclose()

    events = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    received = next(event for event in events if event["event"] == "agent.run.accepted")
    assert received["input_size_bytes"] == 38
    assert "prompt_excerpt" not in received
    assert "prompt_sha256" not in received
    assert received["agent_session_uid"] == "session-1"
    assert received["agent_run_uid"]
    assert received["turn_uid"]


@pytest.mark.asyncio
async def test_runtime_manager_logs_prompt_before_session_load_failure(
    tmp_path,
    capsys,
):
    configure_logging("INFO", machine_sink=True, human_sink=False)
    backend = AsyncMock()
    backend.get_session.return_value = AgentSession(
        uid="session-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.3.1",
    )
    backend.acquire_runtime_lease.side_effect = RuntimeError("lease unavailable")
    manager = SessionRuntimeManager(
        settings=Settings(
            _env_file=None,
            runtime_credential_id="credential-id",
            runtime_credential_secret="credential-secret",
            code_repository_root=tmp_path,
            log_payloads=True,
            tau_runtime_contract="adr48",
        ),
        backend=backend,
        providers=Mock(),
    )

    with pytest.raises(RuntimeError, match="lease unavailable"):
        _ = [
            event
            async for event in manager.prompt(
                "session-1",
                "Explain this failed request.",
            )
        ]
    await manager.aclose()

    events = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    received = next(event for event in events if event["event"] == "agent.run.accepted")
    assert received["input_size_bytes"] == len(b"Explain this failed request.")
    assert "prompt_excerpt" not in received


@pytest.mark.asyncio
async def test_runtime_manager_cancels_timed_out_turn(tmp_path):
    coding_session = _FakeCodingSession(delay=1)
    manager, _backend = _loaded_manager(tmp_path, coding_session, timeout=0.01)

    with pytest.raises(TimeoutError):
        _ = [event async for event in manager.prompt("session-1", "slow")]

    assert coding_session.cancelled is True
    manager._runtimes.clear()
    await manager.aclose()


@pytest.mark.asyncio
async def test_runtime_manager_drains_tracked_background_tasks(tmp_path):
    manager, _backend = _loaded_manager(tmp_path, _FakeCodingSession())
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    async def worker():
        started.set()
        await release.wait()
        completed.set()

    task = manager.create_background_task(worker(), name="a2a-test")
    await started.wait()
    release.set()
    manager._runtimes.clear()
    await manager.aclose()

    assert task.done()
    assert not task.cancelled()
    assert completed.is_set()


@pytest.mark.asyncio
async def test_background_task_detaches_request_and_keeps_causation(tmp_path):
    manager, _backend = _loaded_manager(tmp_path, _FakeCodingSession())
    bind_contextvars(
        request_id="request-1",
        causation_event_id="event-1",
        trace_id="trace-1",
        span_id="span-1",
        user_uid="user-1",
        organization_environment_uid="environment-1",
        agent_session_uid="session-1",
        agent_run_uid="run-1",
        turn_uid="turn-1",
    )

    async def worker():
        return get_contextvars()

    try:
        task = manager.create_background_task(worker(), name="a2a-context-test")
        context = await task
    finally:
        clear_contextvars()

    assert "request_id" not in context
    assert context["origin_request_id"] == "request-1"
    assert context["causation_event_id"] == "event-1"
    assert context["trace_id"] == "trace-1"
    assert context["parent_span_id"] == "span-1"
    assert context["span_id"] != "span-1"
    assert context["organization_environment_uid"] == "environment-1"
    assert context["agent_session_uid"] == "session-1"
    assert context["agent_run_uid"] == "run-1"
    assert context["turn_uid"] == "turn-1"
    assert "project_environment_uid" not in context
    manager._runtimes.clear()
    await manager.aclose()


@pytest.mark.asyncio
async def test_runtime_manager_keeps_shared_mcp_client_on_eviction(tmp_path):
    manager, backend = _loaded_manager(tmp_path, _FakeCodingSession())
    runtime = manager._runtimes["session-1"]
    runtime.mcp_client = AsyncMock()
    manager._mcp_client = runtime.mcp_client

    await manager.evict("session-1")

    runtime.mcp_client.aclose.assert_not_awaited()
    backend.release_runtime_lease.assert_awaited_once()
    await manager.aclose()
    runtime.mcp_client.aclose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_runtime_manager_keeps_shared_mcp_client_when_lease_is_lost(tmp_path):
    manager, backend = _loaded_manager(tmp_path, _FakeCodingSession())
    runtime = manager._runtimes["session-1"]
    runtime.mcp_client = AsyncMock()
    manager._mcp_client = runtime.mcp_client
    runtime.storage.invalidate_lease = Mock()
    manager.settings.runtime_lease_renew_interval_seconds = 0
    backend.renew_runtime_lease.side_effect = RuntimeError("lease lost")

    await manager._renew_lease(runtime)

    assert runtime.lease_lost is True
    runtime.mcp_client.aclose.assert_not_awaited()
    manager._runtimes.clear()
    await manager.aclose()
    runtime.mcp_client.aclose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_runtime_load_keeps_capability_root_and_reuses_backend_auth(tmp_path):
    backend = AsyncMock()
    backend.auth = Mock()
    backend.get_session.return_value = AgentSession(
        uid="session-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.3.1",
    )
    backend.acquire_runtime_lease.return_value = RuntimeLease(
        lease_token="lease-token",
        holder_id="test-holder",
        lease_expires_at="2026-07-25T00:00:00Z",
        checkpoint_version=1,
    )
    backend.get_entries.return_value = SessionEntryList(entries=[], next_sequence=0)
    provider = object()
    providers = Mock()
    providers.for_session = AsyncMock(
        return_value=SimpleNamespace(
            provider=provider,
            name="openai",
            model="gpt-5.4",
            thinking_level=None,
        )
    )
    mcp_client = SimpleNamespace(
        tools=(),
        resources=(),
        aclose=AsyncMock(),
    )
    coding_session = _FakeCodingSession()
    capability_root = tmp_path / "session-capabilities" / ".agents"
    manager = SessionRuntimeManager(
        settings=Settings(
            _env_file=None,
            runtime_credential_id="credential-id",
            runtime_credential_secret="credential-secret",
            code_repository_root=tmp_path,
            tau_runtime_contract="adr48",
        ),
        backend=backend,
        providers=providers,
    )

    with (
        patch(
            "astro.runtime.manager.materialize_session_capabilities",
            AsyncMock(return_value=capability_root),
        ) as materialize,
        patch(
            "astro.runtime.manager.MainSequenceMCPClient.connect",
            AsyncMock(return_value=mcp_client),
        ) as connect,
        patch("astro.runtime.manager.create_mainsequence_mcp_tools", return_value=[]),
        patch("astro.runtime.manager.create_coding_tools", return_value=[]),
        patch("astro.runtime.manager.create_file_tools", return_value=[]),
        patch("astro.runtime.manager.build_web_tools", return_value=[]),
        patch(
            "astro.runtime.manager.CodingSession.load",
            AsyncMock(return_value=coding_session),
        ) as load_coding_session,
    ):
        runtime = await manager.get("session-1")

    materialize.assert_awaited_once()
    connect.assert_awaited_once_with(
        settings=manager.settings,
        auth=backend.auth,
    )
    config = load_coding_session.await_args.args[0]
    assert config.resource_paths.agents_root == capability_root
    assert config.resource_paths.cwd == tmp_path
    assert "ensure_mainsequence_cli_auth" not in {tool.name for tool in config.tools}
    assert "Main Sequence MCP" in config.append_system_prompt
    assert "Main Sequence CLI" not in config.append_system_prompt
    assert "mainsequence-sdk" not in config.append_system_prompt
    assert runtime.mcp_client is mcp_client

    await manager.aclose()


@pytest.mark.asyncio
async def test_runtime_load_runs_independent_startup_io_concurrently(tmp_path):
    backend = AsyncMock()
    backend.auth = Mock()
    backend.get_session.return_value = AgentSession(
        uid="session-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.3.1",
    )
    backend.acquire_runtime_lease.return_value = RuntimeLease(
        lease_token="lease-token",
        holder_id="test-holder",
        lease_expires_at="2026-07-25T00:00:00Z",
        checkpoint_version=1,
    )
    provider_runtime = SimpleNamespace(
        provider=object(),
        name="openai",
        model="gpt-5.4",
        thinking_level=None,
    )
    mcp_client = SimpleNamespace(tools=(), resources=(), aclose=AsyncMock())
    coding_session = _FakeCodingSession()
    started: set[str] = set()
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def block(name, result):
        started.add(name)
        if len(started) == 4:
            all_started.set()
        await release.wait()
        return result

    async def load_provider(*_args, **_kwargs):
        return await block("provider", provider_runtime)

    async def load_history(*_args, **_kwargs):
        return await block(
            "history",
            SessionEntryList(entries=[], next_sequence=0),
        )

    async def load_capabilities(*_args, **_kwargs):
        return await block("capabilities", tmp_path / ".agents")

    async def load_mcp(*_args, **_kwargs):
        return await block("mcp", mcp_client)

    providers = Mock()
    providers.for_session = AsyncMock(side_effect=load_provider)
    backend.get_entries.side_effect = load_history
    manager = SessionRuntimeManager(
        settings=Settings(
            _env_file=None,
            runtime_credential_id="credential-id",
            runtime_credential_secret="credential-secret",
            code_repository_root=tmp_path,
            tau_runtime_contract="adr48",
        ),
        backend=backend,
        providers=providers,
    )

    with (
        patch(
            "astro.runtime.manager.materialize_session_capabilities",
            AsyncMock(side_effect=load_capabilities),
        ),
        patch(
            "astro.runtime.manager.MainSequenceMCPClient.connect",
            AsyncMock(side_effect=load_mcp),
        ),
        patch("astro.runtime.manager.create_mainsequence_mcp_tools", return_value=[]),
        patch("astro.runtime.manager.create_coding_tools", return_value=[]),
        patch("astro.runtime.manager.create_file_tools", return_value=[]),
        patch("astro.runtime.manager.build_web_tools", return_value=[]),
        patch(
            "astro.runtime.manager.CodingSession.load",
            AsyncMock(return_value=coding_session),
        ),
    ):
        load_task = asyncio.create_task(manager.get("session-1"))
        await asyncio.wait_for(all_started.wait(), timeout=1)
        assert started == {"provider", "history", "capabilities", "mcp"}
        release.set()
        await load_task

    await manager.aclose()


@pytest.mark.asyncio
async def test_coding_session_load_failure_keeps_process_mcp_until_shutdown(tmp_path):
    backend = AsyncMock()
    backend.auth = Mock()
    backend.get_session.return_value = AgentSession(
        uid="session-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.3.1",
    )
    backend.acquire_runtime_lease.return_value = RuntimeLease(
        lease_token="lease-token",
        holder_id="test-holder",
        lease_expires_at="2026-07-25T00:00:00Z",
        checkpoint_version=1,
    )
    backend.get_entries.return_value = SessionEntryList(entries=[], next_sequence=0)
    providers = Mock()
    providers.for_session = AsyncMock(
        return_value=SimpleNamespace(
            provider=object(),
            name="openai",
            model="gpt-5.4",
            thinking_level=None,
        )
    )
    mcp_client = SimpleNamespace(
        tools=(),
        resources=(),
        aclose=AsyncMock(),
    )
    manager = SessionRuntimeManager(
        settings=Settings(
            _env_file=None,
            runtime_credential_id="credential-id",
            runtime_credential_secret="credential-secret",
            code_repository_root=tmp_path,
            tau_runtime_contract="adr48",
        ),
        backend=backend,
        providers=providers,
    )

    with (
        patch(
            "astro.runtime.manager.materialize_session_capabilities",
            AsyncMock(return_value=tmp_path / ".agents"),
        ),
        patch(
            "astro.runtime.manager.MainSequenceMCPClient.connect",
            AsyncMock(return_value=mcp_client),
        ),
        patch("astro.runtime.manager.create_mainsequence_mcp_tools", return_value=[]),
        patch("astro.runtime.manager.create_coding_tools", return_value=[]),
        patch("astro.runtime.manager.create_file_tools", return_value=[]),
        patch("astro.runtime.manager.build_web_tools", return_value=[]),
        patch(
            "astro.runtime.manager.CodingSession.load",
            AsyncMock(side_effect=RuntimeError("coding session load failed")),
        ),
    ):
        with pytest.raises(RuntimeError, match="coding session load failed"):
            await manager.get("session-1")

    assert "session-1" not in manager._runtimes
    mcp_client.aclose.assert_not_awaited()
    backend.release_runtime_lease.assert_awaited_once()
    await manager.aclose()
    mcp_client.aclose.assert_awaited_once_with()
