"""Concurrent, lease-aware Tau session runtime manager."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
import uuid
from collections.abc import AsyncIterator, Coroutine
from pathlib import Path
from typing import Any

import httpx
import structlog
from structlog.contextvars import (
    bind_contextvars,
    bound_contextvars,
    clear_contextvars,
    get_contextvars,
)
from tau_coding import CodingSession, CodingSessionConfig
from tau_coding.resources import TauResourcePaths
from tau_coding.tools import create_coding_tools
from tau_file_tools import create_file_tools
from tau_web_access import MemorySearchResultStore

from astro.backend.client import MainSequenceClient
from astro.backend.mcp import MainSequenceMCPClient
from astro.backend.models import (
    AgentRuntimeActivity,
    ProviderExecutionEvidence,
    RuntimeActivityPatch,
    RuntimeLeaseReleaseRequest,
    RuntimeLeaseRenewRequest,
    TauRuntimeBootstrapRequest,
    TauTurnCommit,
)
from astro.capabilities import (
    known_capability_hashes,
    materialize_bootstrap_capabilities,
)
from astro.errors import BackendConflictError, ConfigurationError, LeaseLostError
from astro.logging import conversation_log_fields
from astro.providers.factory import ProviderFactory
from astro.resources.loader import append_system_prompt, resource_root
from astro.runtime.events import AstroRuntimeEvent
from astro.runtime.observability import TauTurnObserver
from astro.runtime.provenance import TurnProvenance
from astro.runtime.snapshots import (
    SNAPSHOT_SCHEMA_VERSION,
    TAU_RUNTIME_VERSION,
    build_snapshot_upload,
    restore_snapshot,
)
from astro.sessions.storage import SESSION_ENTRY_ADAPTER, BackendSessionStorage
from astro.settings import Settings
from astro.tools.mainsequence_mcp import (
    create_mainsequence_mcp_tools,
    mainsequence_mcp_resource_prompt,
)
from astro.tools.runtime_info import create_runtime_info_tool
from astro.tools.web_access import build_web_tools

from .session import ActiveSessionRuntime

logger = structlog.get_logger(__name__)
ADR49_RUNTIME_CAPABILITIES = {
    "tau_runtime_bootstrap": "v2",
    "tau_resume_snapshot": "v1",
    "tau_activity_sequence": "v1",
    "tau_turn_commit": "v1",
}


class SessionRuntimeManager:
    def __init__(
        self,
        *,
        settings: Settings,
        backend: MainSequenceClient,
        providers: ProviderFactory,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.providers = providers
        self.holder_id = f"{socket.gethostname()}:{uuid.uuid4().hex}"
        self._runtimes: dict[str, ActiveSessionRuntime] = {}
        self._load_locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()
        self._eviction_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[object]] = set()
        self._web_client = httpx.AsyncClient(timeout=60)
        self._mcp_client: MainSequenceMCPClient | None = None
        self._mcp_lock = asyncio.Lock()
        self._startup_ready = False
        self._snapshot_restore_count = 0
        self._snapshot_fallback_count = 0
        self._snapshot_upload_count = 0
        self._draining = False
        self._closed = False

    async def start(self) -> None:
        if self.settings.startup_dependencies_enabled:
            await self.backend.auth.prefetch()
            await self._get_mcp_client()
        self._startup_ready = True
        if self._eviction_task is None:
            self._eviction_task = asyncio.create_task(
                self._eviction_loop(),
                name="astro-session-eviction",
            )

    def snapshot(self) -> dict[str, object]:
        return {
            "holder_id": self.holder_id,
            "loaded_sessions": len(self._runtimes),
            "working_sessions": sum(
                runtime.runtime_activity == "working" for runtime in self._runtimes.values()
            ),
            "lease_lost_sessions": sum(runtime.lease_lost for runtime in self._runtimes.values()),
            "startup_ready": self._startup_ready,
            "mcp_tool_count": len(self._mcp_client.tools) if self._mcp_client else 0,
            "mcp_resource_count": len(self._mcp_client.resources) if self._mcp_client else 0,
            "snapshot_restore_count": self._snapshot_restore_count,
            "snapshot_fallback_count": self._snapshot_fallback_count,
            "snapshot_upload_count": self._snapshot_upload_count,
        }

    async def _get_mcp_client(self) -> MainSequenceMCPClient:
        if self._mcp_client is not None:
            return self._mcp_client
        async with self._mcp_lock:
            if self._mcp_client is None:
                started_at = time.monotonic()
                self._mcp_client = await MainSequenceMCPClient.connect(
                    settings=self.settings,
                    auth=self.backend.auth,
                )
                logger.info(
                    "runtime.mcp.catalog.completed",
                    tool_count=len(self._mcp_client.tools),
                    resource_count=len(self._mcp_client.resources),
                    duration_ms=round((time.monotonic() - started_at) * 1000, 3),
                    outcome="success",
                )
        return self._mcp_client

    async def get(self, session_uid: str) -> ActiveSessionRuntime:
        if self._draining or self._closed:
            raise RuntimeError("Astro runtime is shutting down")
        runtime = self._runtimes.get(session_uid)
        if runtime is not None and not runtime.evicting and not runtime.lease_lost:
            return runtime
        async with self._registry_lock:
            load_lock = self._load_locks.setdefault(session_uid, asyncio.Lock())
        async with load_lock:
            runtime = self._runtimes.get(session_uid)
            if runtime is not None and not runtime.evicting and not runtime.lease_lost:
                return runtime
            return await self._load(session_uid)

    async def _load(self, session_uid: str) -> ActiveSessionRuntime:
        with bound_contextvars(
            session_uid=session_uid,
            agent_session_uid=session_uid,
        ):
            return await self._load_with_context(session_uid)

    async def _load_with_context(
        self,
        session_uid: str,
    ) -> ActiveSessionRuntime:
        return await self._load_v2(session_uid)

    async def _load_v2(self, session_uid: str) -> ActiveSessionRuntime:
        load_started_at = time.monotonic()
        bootstrap_request_uid = str(uuid.uuid4())
        logger.info(
            "runtime.session.load.started",
            message="Loading Tau session runtime through bootstrap v2",
            session_uid=session_uid,
            holder_id=self.holder_id,
            bootstrap_request_uid=bootstrap_request_uid,
        )
        bootstrap = await self.backend.bootstrap_tau_runtime(
            session_uid,
            TauRuntimeBootstrapRequest(
                holder_id=self.holder_id,
                ttl_seconds=self.settings.runtime_lease_ttl_seconds,
                bootstrap_request_uid=bootstrap_request_uid,
                known_capability_hashes=known_capability_hashes(self.settings.session_asset_root),
                supported_snapshot_schema_versions=[SNAPSHOT_SCHEMA_VERSION],
                supported_provider_control_schema_versions=[1],
                tau_runtime_version=TAU_RUNTIME_VERSION,
            ),
        )
        missing_capabilities = {
            name: version
            for name, version in ADR49_RUNTIME_CAPABILITIES.items()
            if bootstrap.runtime_capabilities.get(name) != version
        }
        if missing_capabilities:
            raise ConfigurationError(
                "Backend does not advertise the complete Tau bootstrap v2 contract: "
                + ", ".join(sorted(missing_capabilities))
            )
        session = bootstrap.session
        session_extra = session.model_extra or {}
        if session.harness != "tau":
            raise ConfigurationError(
                f"Session {session_uid} uses harness {session.harness!r}; "
                "this Tau runtime can only resume Tau sessions"
            )
        lease = bootstrap.lease
        try:
            snapshot_selected = bootstrap.resume_snapshot is not None
            fallback_reason = (
                bootstrap.history.model_extra.get("snapshot_fallback_reason")
                if bootstrap.history.model_extra
                else None
            )
            if bootstrap.resume_snapshot is not None:
                restore_started_at = time.monotonic()
                try:
                    restored = restore_snapshot(
                        snapshot=bootstrap.resume_snapshot,
                        history_delta=bootstrap.history,
                        runtime_config_sha256=str(session_extra.get("runtime_config_sha256", "")),
                        capability_set_sha256=str(session_extra.get("capability_set_sha256", "")),
                    )
                    initial_entries = restored.entries
                    initial_next_sequence = restored.next_sequence
                    self._snapshot_restore_count += 1
                    logger.info(
                        "runtime.snapshot.restore.completed",
                        agent_session_uid=session_uid,
                        base_sequence=bootstrap.resume_snapshot.base_sequence,
                        delta_entry_count=len(bootstrap.history.entries),
                        duration_ms=round(
                            (time.monotonic() - restore_started_at) * 1000,
                            3,
                        ),
                        outcome="success",
                    )
                except (BackendConflictError, ValueError) as error:
                    snapshot_selected = False
                    fallback_reason = type(error).__name__
                    self._snapshot_fallback_count += 1
                    durable = await self.backend.get_entries(session_uid)
                    initial_entries = [
                        SESSION_ENTRY_ADAPTER.validate_python(record.entry_json)
                        for record in durable.entries
                    ]
                    initial_next_sequence = durable.next_sequence
                    logger.warning(
                        "runtime.snapshot.restore.completed",
                        agent_session_uid=session_uid,
                        outcome="fallback",
                        fallback_reason=fallback_reason,
                        duration_ms=round(
                            (time.monotonic() - restore_started_at) * 1000,
                            3,
                        ),
                    )
            else:
                self._snapshot_fallback_count += 1
                durable = (
                    await self.backend.get_entries(session_uid)
                    if bootstrap.history.has_more
                    else bootstrap.history
                )
                initial_entries = [
                    SESSION_ENTRY_ADAPTER.validate_python(record.entry_json)
                    for record in durable.entries
                ]
                initial_next_sequence = durable.next_sequence

            storage = BackendSessionStorage(
                backend=self.backend,
                session_uid=session_uid,
                lease_token=lease.lease_token,
                holder_id=self.holder_id,
                initial_entries=initial_entries,
                initial_next_sequence=initial_next_sequence,
            )
            provider_name = session.active_provider or ""
            credential = self.backend.provider_credential_from_hydration(
                provider_name,
                bootstrap.provider_credentials,
            )
            provider_runtime = self.providers.for_session_credential(
                session,
                holder_id=self.holder_id,
                evidence=ProviderExecutionEvidence(
                    credential=credential,
                    provider_control=bootstrap.provider_control,
                ),
            )
            async with asyncio.TaskGroup() as startup_tasks:
                capability_task = startup_tasks.create_task(
                    materialize_bootstrap_capabilities(
                        bindings=bootstrap.capabilities,
                        session_uid=session_uid,
                        asset_root=self.settings.session_asset_root,
                    ),
                    name=f"astro-capabilities-bootstrap-{session_uid}",
                )
                mcp_task = startup_tasks.create_task(
                    self._get_mcp_client(),
                    name=f"astro-mcp-shared-{session_uid}",
                )
            session_agents_root = capability_task.result()
            mcp_client = mcp_task.result()
            cwd = self._resolve_cwd()
            web_store = MemorySearchResultStore()
            tools = [
                *create_coding_tools(cwd=cwd),
                *create_file_tools(cwd=cwd),
                *build_web_tools(cwd=cwd, store=web_store, client=self._web_client),
                *create_mainsequence_mcp_tools(
                    mcp_client,
                    caller_session_proof={
                        "caller_agent_session_uid": session_uid,
                        "lease_holder_id": lease.holder_id,
                        "lease_token": lease.lease_token,
                    },
                ),
                create_runtime_info_tool(
                    session_uid=session_uid,
                    cwd=cwd,
                    provider=provider_runtime.name,
                    model=provider_runtime.model,
                ),
            ]
            coding_session = await CodingSession.load(
                CodingSessionConfig(
                    provider=provider_runtime.provider,
                    provider_name=provider_runtime.name,
                    model=provider_runtime.model,
                    thinking_level=provider_runtime.thinking_level,
                    storage=storage,
                    cwd=cwd,
                    tools=tools,
                    session_id=session_uid,
                    append_system_prompt=append_system_prompt(
                        extra_context=mainsequence_mcp_resource_prompt(mcp_client),
                    ),
                    resource_paths=TauResourcePaths(
                        root=resource_root(),
                        cwd=cwd,
                        agents_root=session_agents_root,
                    ),
                    project_extensions_enabled=False,
                )
            )
            runtime = ActiveSessionRuntime(
                session_uid=session_uid,
                holder_id=self.holder_id,
                coding_session=coding_session,
                storage=storage,
                provider=provider_runtime.provider,
                provider_name=provider_runtime.name,
                model=provider_runtime.model,
                mcp_client=mcp_client,
                runtime_config_sha256=str(session_extra.get("runtime_config_sha256", "")),
                capability_set_sha256=str(session_extra.get("capability_set_sha256", "")),
                provider_control_schema=bootstrap.provider_control.schema_version,
                catalog_digest=bootstrap.provider_control.catalog_digest,
            )
            state = bootstrap.runtime_state
            runtime.runtime_activity = state.runtime_activity or "loading"
            runtime.activity_revision = state.activity_revision or 0
            runtime.activity_sequence = state.activity_sequence or 0
            runtime.active_turn_uid = state.active_turn_uid
            self._schedule_activity(
                runtime,
                runtime_activity="idle",
                active_turn_uid=None,
            )
            runtime.lease_renew_task = asyncio.create_task(
                self._renew_lease(runtime),
                name=f"astro-lease-{session_uid}",
            )
            self._runtimes[session_uid] = runtime
            logger.info(
                "runtime.session.load.completed",
                message="Loaded Tau session runtime through bootstrap v2",
                session_uid=session_uid,
                provider=provider_runtime.name,
                model=provider_runtime.model,
                snapshot_selected=snapshot_selected,
                snapshot_fallback_reason=fallback_reason,
                history_entry_count=len(initial_entries),
                mcp_tool_count=len(mcp_client.tools),
                mcp_resource_count=len(mcp_client.resources),
                provider_control_schema=bootstrap.provider_control.schema_version,
                catalog_digest=bootstrap.provider_control.catalog_digest,
                contract="tau-bootstrap-v2",
                duration_ms=round((time.monotonic() - load_started_at) * 1000, 3),
            )
            return runtime
        except BaseException:
            with contextlib.suppress(Exception):
                await self.backend.release_runtime_lease(
                    session_uid,
                    RuntimeLeaseReleaseRequest(
                        lease_token=lease.lease_token,
                        holder_id=self.holder_id,
                        reason="runtime_load_failed",
                    ),
                )
            raise

    def _resolve_cwd(self) -> Path:
        code_repository_root = self.settings.code_repository_root.resolve()
        if not code_repository_root.exists() or not code_repository_root.is_dir():
            raise ValueError(f"ASTRO_CODE_REPOSITORY_CWD does not exist: {code_repository_root}")
        return code_repository_root

    async def prompt(
        self,
        session_uid: str,
        content: str,
        *,
        provenance: TurnProvenance | None = None,
    ) -> AsyncIterator[AstroRuntimeEvent]:
        agent_run_uid = str(uuid.uuid4())
        turn_uid = str(uuid.uuid4())
        with bound_contextvars(
            session_uid=session_uid,
            agent_session_uid=session_uid,
            agent_run_uid=agent_run_uid,
            turn_uid=turn_uid,
            agent_uid="astro-tau",
        ):
            async for event in self._prompt_with_context(
                session_uid, content, provenance=provenance
            ):
                yield event

    async def _prompt_with_context(
        self,
        session_uid: str,
        content: str,
        *,
        provenance: TurnProvenance | None = None,
    ) -> AsyncIterator[AstroRuntimeEvent]:
        logger.info(
            "agent.run.accepted",
            message="Accepted Tau agent run",
            session_uid=session_uid,
            agent_session_uid=session_uid,
            agent_type="tau_coding",
            is_streaming=True,
            **conversation_log_fields(
                content,
                include_excerpt=self.settings.log_payloads,
            ),
        )
        yield AstroRuntimeEvent(
            type="lifecycle",
            data={"phase": "loading_session"},
        )
        runtime = await self.get(session_uid)
        if runtime.lease_lost:
            raise LeaseLostError(f"Runtime lease was lost for session {session_uid}")
        if runtime.cancellation_requested:
            raise LeaseLostError(f"Runtime cancellation was requested for session {session_uid}")
        context = get_contextvars()
        turn_uid = str(context.get("turn_uid") or uuid.uuid4())
        runtime = await self._begin_turn_with_one_reload(
            session_uid=session_uid,
            runtime=runtime,
            turn_uid=turn_uid,
        )
        yield AstroRuntimeEvent(type="lifecycle", data={"phase": "generating"})

        started_at = time.monotonic()
        terminal_status = "completed"
        error_type: str | None = None
        output_complete = False
        observer = TauTurnObserver(
            provider=runtime.provider_name,
            model=runtime.model,
        )
        logger.info(
            "agent.run.started",
            message="Started Tau agent run",
            session_uid=session_uid,
            agent_session_uid=session_uid,
            agent_type="tau_coding",
            provider=runtime.provider_name,
            model=runtime.model,
            is_streaming=True,
        )
        logger.info(
            "agent.turn.started",
            message="Started Tau turn",
            agent_session_uid=session_uid,
            agent_type="tau_coding",
            provider=runtime.provider_name,
            model=runtime.model,
            is_streaming=True,
        )
        try:
            try:
                async with asyncio.timeout(self.settings.turn_timeout_seconds):
                    async for event in runtime.prompt(
                        content,
                        provenance=provenance,
                        durability_task=lambda: self.create_background_task(
                            self._settle_turn_durability(runtime, turn_uid),
                            name=f"astro-turn-persist-{session_uid}-{turn_uid}",
                        ),
                    ):
                        observer.observe(event)
                        if event.type == "agent_settled":
                            output_complete = True
                        yield event
                    if output_complete and runtime.persistence_task is not None:
                        commit = runtime.persistence_task.result()
                        if not isinstance(commit, TauTurnCommit):
                            raise RuntimeError("Tau durability task omitted its turn commit")
                        runtime.pending_snapshot_commit = commit
            except TimeoutError:
                terminal_status = "timed_out"
                error_type = "TimeoutError"
                runtime.cancel(force=True)
                raise
        except asyncio.CancelledError:
            if not output_complete:
                terminal_status = "cancelled"
                error_type = "CancelledError"
            raise
        except Exception as error:
            if terminal_status == "completed":
                terminal_status = "failed"
            error_type = type(error).__name__
            raise
        finally:
            if terminal_status == "completed" and observer.terminal_failure is not None:
                failure = observer.terminal_failure
                terminal_status = "cancelled" if failure.reason == "aborted" else "failed"
                error_type = failure.error_type
            if not output_complete and runtime.runtime_activity == "working":
                self._schedule_activity(
                    runtime,
                    runtime_activity="idle",
                    active_turn_uid=None,
                )
            log = logger.info if terminal_status == "completed" else logger.warning
            outcome = (
                "success"
                if terminal_status == "completed"
                else "cancelled"
                if terminal_status == "cancelled"
                else "failed"
            )
            terminal_event = (
                "completed"
                if terminal_status == "completed"
                else "cancelled"
                if terminal_status == "cancelled"
                else "failed"
            )
            terminal_fields = {
                "agent_session_uid": session_uid,
                "agent_type": "tau_coding",
                "provider": runtime.provider_name,
                "model": runtime.model,
                "terminal_status": terminal_status,
                "outcome": outcome,
                "error_type": error_type,
                "duration_ms": round(
                    (time.monotonic() - started_at) * 1000,
                    3,
                ),
                **observer.terminal_fields(),
            }
            log(
                f"agent.turn.{terminal_event}",
                message="Finished Tau turn",
                **terminal_fields,
            )
            log(
                f"agent.run.{terminal_event}",
                message="Finished Tau agent run",
                **terminal_fields,
            )

    async def _begin_turn_with_one_reload(
        self,
        *,
        session_uid: str,
        runtime: ActiveSessionRuntime,
        turn_uid: str,
    ) -> ActiveSessionRuntime:
        for begin_attempt in range(2):
            runtime.activity_sequence += 1
            try:
                state = await runtime.storage.begin_turn(
                    turn_uid=turn_uid,
                    activity_sequence=runtime.activity_sequence,
                )
            except BackendConflictError:
                if begin_attempt:
                    raise
                runtime.storage.invalidate_lease()
                await self.evict(session_uid)
                runtime = await self.get(session_uid)
                continue
            runtime.runtime_activity = state.runtime_activity or "working"
            runtime.active_turn_uid = state.active_turn_uid
            runtime.activity_sequence = state.activity_sequence or runtime.activity_sequence
            return runtime
        raise AssertionError("Tau turn reload loop did not terminate")

    async def _transition_runtime_activity(
        self,
        runtime: ActiveSessionRuntime,
        *,
        runtime_activity: AgentRuntimeActivity,
        active_turn_uid: str | None,
    ) -> None:
        state = await self.backend.patch_runtime_activity(
            runtime.session_uid,
            RuntimeActivityPatch(
                holder_id=self.holder_id,
                lease_token=runtime.storage.lease_token,
                expected_activity_revision=runtime.activity_revision,
                runtime_activity=runtime_activity,
                active_turn_uid=active_turn_uid,
            ),
        )
        runtime.runtime_activity = state.runtime_activity or runtime_activity
        runtime.active_turn_uid = state.active_turn_uid
        if state.activity_revision is None:
            raise RuntimeError("Backend omitted Tau activity_revision")
        runtime.activity_revision = state.activity_revision

    def _schedule_activity(
        self,
        runtime: ActiveSessionRuntime,
        *,
        runtime_activity: AgentRuntimeActivity,
        active_turn_uid: str | None,
    ) -> int:
        runtime.activity_sequence += 1
        sequence = runtime.activity_sequence
        runtime.runtime_activity = runtime_activity
        runtime.active_turn_uid = active_turn_uid
        self.create_background_task(
            self._publish_activity(
                runtime,
                runtime_activity=runtime_activity,
                active_turn_uid=active_turn_uid,
                activity_sequence=sequence,
            ),
            name=(f"astro-activity-{runtime.session_uid}-{sequence}-{runtime_activity}"),
        )
        return sequence

    async def _publish_activity(
        self,
        runtime: ActiveSessionRuntime,
        *,
        runtime_activity: AgentRuntimeActivity,
        active_turn_uid: str | None,
        activity_sequence: int,
    ) -> object:
        started_at = time.monotonic()
        state = await self.backend.patch_runtime_activity(
            runtime.session_uid,
            RuntimeActivityPatch(
                holder_id=self.holder_id,
                lease_token=runtime.storage.lease_token,
                activity_sequence=activity_sequence,
                runtime_activity=runtime_activity,
                active_turn_uid=active_turn_uid,
            ),
        )
        logger.info(
            "runtime.activity.publish.completed",
            agent_session_uid=runtime.session_uid,
            turn_uid=active_turn_uid,
            activity_sequence=activity_sequence,
            runtime_activity=runtime_activity,
            applied=state.applied,
            duration_ms=round((time.monotonic() - started_at) * 1000, 3),
            outcome="success",
        )
        return state

    async def _settle_turn_durability(
        self,
        runtime: ActiveSessionRuntime,
        turn_uid: str,
    ) -> object:
        runtime.activity_sequence += 1
        commit_sequence = runtime.activity_sequence
        try:
            commit = await runtime.storage.commit_turn(
                turn_uid=turn_uid,
                activity_sequence=commit_sequence,
            )
        except Exception as error:
            logger.exception(
                "runtime.session.persistence.failed",
                message="Tau turn output completed but persistence failed",
                agent_session_uid=runtime.session_uid,
                persistence_phase="durability",
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise
        runtime.runtime_activity = "idle"
        runtime.active_turn_uid = None
        logger.info(
            "runtime.persistence.durable",
            message="Tau turn entries are durable",
            agent_session_uid=runtime.session_uid,
            turn_uid=turn_uid,
            next_sequence=commit.next_sequence,
            persistence_phase="durability",
        )
        return commit

    async def _upload_resume_snapshot(
        self,
        runtime: ActiveSessionRuntime,
        commit: TauTurnCommit,
    ) -> object:
        started_at = time.monotonic()
        entries = await runtime.storage.entries_at_sequence(commit.next_sequence)
        request = build_snapshot_upload(
            holder_id=self.holder_id,
            lease_token=runtime.storage.lease_token,
            entries=entries,
            base_sequence=commit.next_sequence,
            last_committed_turn_uid=commit.turn_uid,
            runtime_config_sha256=runtime.runtime_config_sha256,
            capability_set_sha256=runtime.capability_set_sha256,
        )
        result = await self.backend.upload_tau_resume_snapshot(
            runtime.session_uid,
            request,
        )
        if result.applied:
            self._snapshot_upload_count += 1
        logger.info(
            "runtime.snapshot.upload.completed",
            agent_session_uid=runtime.session_uid,
            turn_uid=commit.turn_uid,
            base_sequence=commit.next_sequence,
            canonical_bytes=len(request.model_dump_json(exclude={"lease_token"}).encode("utf-8")),
            applied=result.applied,
            replayed=result.replayed,
            duration_ms=round((time.monotonic() - started_at) * 1000, 3),
            outcome="success",
        )
        return result

    async def cancel(self, session_uid: str) -> bool:
        runtime = self._runtimes.get(session_uid)
        if runtime is None:
            return False
        runtime.cancellation_requested = True
        cancelled = runtime.cancel()
        return cancelled

    def mark_response_delivered(self, session_uid: str) -> bool:
        """Schedule the latest durable snapshot after the transport terminal event."""
        runtime = self._runtimes.get(session_uid)
        if runtime is None or runtime.pending_snapshot_commit is None:
            return False
        commit = runtime.pending_snapshot_commit
        runtime.pending_snapshot_commit = None
        self.create_background_task(
            self._upload_resume_snapshot(runtime, commit),
            name=f"astro-snapshot-upload-{session_uid}-{commit.turn_uid}",
        )
        logger.info(
            "runtime.snapshot.upload.scheduled",
            agent_session_uid=session_uid,
            turn_uid=commit.turn_uid,
            base_sequence=commit.next_sequence,
            outcome="accepted",
        )
        return True

    def create_background_task(
        self,
        coroutine: Coroutine[Any, Any, object],
        *,
        name: str,
        operation_uid: str | None = None,
    ) -> asyncio.Task[object]:
        if self._draining or self._closed:
            coroutine.close()
            raise RuntimeError("Astro runtime is shutting down")
        parent = get_contextvars()
        detached_fields = {
            field: parent[field]
            for field in (
                "trace_id",
                "user_uid",
                "principal_type",
                "coding_agent_service_uid",
                "agent_session_uid",
                "agent_uid",
                "agent_run_uid",
                "turn_uid",
                "code_repository_uid",
                "organization_environment_uid",
            )
            if parent.get(field) is not None
        }
        detached_fields.update(
            operation_uid=operation_uid or str(uuid.uuid4()),
            causation_event_id=parent.get("causation_event_id"),
            origin_request_id=parent.get("request_id"),
            parent_span_id=parent.get("span_id"),
            span_id=uuid.uuid4().hex[:16],
        )

        async def run_detached() -> object:
            clear_contextvars()
            bind_contextvars(
                **{key: value for key, value in detached_fields.items() if value is not None}
            )
            try:
                return await coroutine
            finally:
                clear_contextvars()

        task = asyncio.create_task(run_detached(), name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_task_done)
        return task

    def _background_task_done(self, task: asyncio.Task[object]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "runtime.background.failed",
                message="Background runtime task failed",
                task_name=task.get_name(),
                error_type=type(error).__name__,
                error_message=str(error),
            )

    async def _drain_background_tasks(self) -> None:
        tasks = tuple(self._background_tasks)
        if not tasks:
            return
        _done, pending = await asyncio.wait(
            tasks,
            timeout=self.settings.shutdown_grace_seconds,
        )
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        logger.info(
            "runtime.shutdown.background_drained",
            message="Drained background runtime tasks",
            completed_count=len(tasks) - len(pending),
            cancelled_count=len(pending),
        )

    async def _renew_lease(self, runtime: ActiveSessionRuntime) -> None:
        clear_contextvars()
        with bound_contextvars(
            session_uid=runtime.session_uid,
            agent_session_uid=runtime.session_uid,
            operation_uid=str(uuid.uuid4()),
        ):
            await self._renew_lease_with_context(runtime)

    async def _renew_lease_with_context(
        self,
        runtime: ActiveSessionRuntime,
    ) -> None:
        try:
            while not runtime.evicting and not self._closed:
                await asyncio.sleep(self.settings.runtime_lease_renew_interval_seconds)
                lease = await self.backend.renew_runtime_lease(
                    runtime.session_uid,
                    RuntimeLeaseRenewRequest(
                        lease_token=runtime.storage.lease_token,
                        holder_id=self.holder_id,
                        ttl_seconds=self.settings.runtime_lease_ttl_seconds,
                    ),
                )
                runtime.storage.replace_lease_token(lease.lease_token)
                if lease.cancel_requested:
                    runtime.cancellation_requested = True
                    runtime.cancel(force=True)
                    self.create_background_task(
                        self._evict_after_cancellation(runtime),
                        name=f"astro-cancel-evict-{runtime.session_uid}",
                    )
                    return
                logger.debug(
                    "runtime.lease.renewed",
                    message="Renewed backend runtime lease",
                    session_uid=runtime.session_uid,
                    checkpoint_version=lease.checkpoint_version,
                    lease_expires_at=lease.lease_expires_at,
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            runtime.lease_lost = True
            runtime.storage.invalidate_lease()
            runtime.cancel()
            logger.exception(
                "runtime.lease.lost",
                message="Lost backend runtime lease",
                session_uid=runtime.session_uid,
                error_type=type(error).__name__,
                error_message=str(error),
            )

    async def _evict_after_cancellation(
        self,
        runtime: ActiveSessionRuntime,
    ) -> object:
        async with runtime.lock:
            pass
        await self.evict(runtime.session_uid)
        return None

    async def _eviction_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(self.settings.session_eviction_interval_seconds)
                cutoff = time.monotonic() - self.settings.session_idle_ttl_seconds
                candidates = [
                    session_uid
                    for session_uid, runtime in self._runtimes.items()
                    if runtime.last_used_at < cutoff and not runtime.lock.locked()
                ]
                for session_uid in candidates:
                    await self.evict(session_uid)
        except asyncio.CancelledError:
            raise

    async def evict(self, session_uid: str) -> None:
        with bound_contextvars(session_uid=session_uid):
            await self._evict_with_context(session_uid)

    async def _evict_with_context(self, session_uid: str) -> None:
        runtime = self._runtimes.get(session_uid)
        if runtime is None:
            return
        runtime.evicting = True
        if runtime.lock.locked():
            runtime.cancel()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self.settings.shutdown_grace_seconds):
                    async with runtime.lock:
                        pass
        persistence_flushed = False
        try:
            async with asyncio.timeout(self.settings.shutdown_grace_seconds):
                if runtime.persistence_task is not None:
                    await asyncio.shield(runtime.persistence_task)
                else:
                    await runtime.storage.flush()
            persistence_flushed = True
        except Exception as error:
            logger.exception(
                "runtime.session.persistence_flush_failed",
                message="Could not flush Tau entries before runtime eviction",
                session_uid=session_uid,
                error_type=type(error).__name__,
                error_message=str(error),
            )
        if runtime.lease_renew_task:
            runtime.lease_renew_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runtime.lease_renew_task
        if persistence_flushed:
            with contextlib.suppress(Exception):
                await self.backend.release_runtime_lease(
                    session_uid,
                    RuntimeLeaseReleaseRequest(
                        lease_token=runtime.storage.lease_token,
                        holder_id=self.holder_id,
                        reason="runtime_eviction",
                    ),
                )
                logger.info(
                    "runtime.lease.released",
                    message="Released backend runtime lease",
                    session_uid=session_uid,
                    holder_id=self.holder_id,
                )
        close = getattr(runtime.provider, "aclose", None)
        if close is not None:
            with contextlib.suppress(Exception):
                await close()
        self._runtimes.pop(session_uid, None)
        logger.info(
            "runtime.session.evicted",
            message="Evicted Tau session runtime",
            session_uid=session_uid,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._draining = True
        if self._eviction_task:
            self._eviction_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._eviction_task
        await self._drain_background_tasks()
        self._closed = True
        await asyncio.gather(
            *(self.evict(session_uid) for session_uid in list(self._runtimes)),
            return_exceptions=True,
        )
        if self._mcp_client is not None:
            with contextlib.suppress(Exception):
                await self._mcp_client.aclose()
        await self._web_client.aclose()
