"""Concurrent, lease-aware Tau session runtime manager."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
import uuid
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from structlog.contextvars import (
    bind_contextvars,
    bound_contextvars,
    clear_contextvars,
    get_contextvars,
)
from tau_coding import CodingSession, CodingSessionConfig
from tau_coding.tools import create_coding_tools

from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.mcp import MainSequenceMCPClient
from ms_tau_sdk.backend.models import (
    AgentRuntimeActivity,
    ProviderExecutionEvidence,
    RuntimeActivityPatch,
    RuntimeLeaseReleaseRequest,
    RuntimeLeaseRenewRequest,
    TauRuntimeBootstrapRequest,
    TauTurnCommit,
)
from ms_tau_sdk.errors import BackendConflictError, ConfigurationError, LeaseLostError
from ms_tau_sdk.logging import conversation_log_fields
from ms_tau_sdk.providers.factory import ProviderFactory
from ms_tau_sdk.resources.loader import tau_resource_paths
from ms_tau_sdk.runtime.events import TauRuntimeEvent
from ms_tau_sdk.runtime.extensions import ProjectExtensionState
from ms_tau_sdk.runtime.observability import TauTurnObserver
from ms_tau_sdk.runtime.provenance import TurnProvenance
from ms_tau_sdk.runtime.snapshots import (
    SNAPSHOT_SCHEMA_VERSION,
    TAU_RUNTIME_VERSION,
    build_snapshot_upload,
    restore_snapshot,
)
from ms_tau_sdk.sessions.storage import SESSION_ENTRY_ADAPTER, BackendSessionStorage
from ms_tau_sdk.settings import TauSDKSettings
from ms_tau_sdk.tools.mainsequence_mcp import (
    CALLER_SESSION_PROOF_REQUIRED_META_KEY,
    create_mainsequence_mcp_tools,
    mainsequence_mcp_resource_prompt,
)
from ms_tau_sdk.tools.task_control import create_task_control_tools

from .session import ActiveSessionRuntime, PlatformEvent

logger = structlog.get_logger(__name__)
RUNTIME_CAPABILITIES = {
    "tau_runtime_bootstrap": "v3",
    "tau_resume_snapshot": "v2",
    "tau_activity_sequence": "v1",
    "tau_turn_commit": "v1",
}


@dataclass(frozen=True, slots=True)
class RuntimeExecutionFence:
    holder_id: str
    lease_token: str


def _repository_relative_path(path: Path | None, *, cwd: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(cwd.resolve()).as_posix()
    except ValueError:
        return None


def _extension_diagnostic_type(message: str) -> str:
    normalized = message.lower()
    if "failed to import" in normalized:
        return "import_error"
    if "setup failed" in normalized:
        return "setup_error"
    if "duplicate" in normalized or "already registered" in normalized:
        return "registration_conflict"
    if "does not exist" in normalized:
        return "missing_entry"
    if "must be" in normalized or "does not define" in normalized:
        return "invalid_extension"
    return "tau_extension_diagnostic"


class SessionRuntimeManager:
    def __init__(
        self,
        *,
        settings: TauSDKSettings,
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
        self._a2a_task_executions: dict[str, asyncio.Task[object]] = {}
        self._a2a_caller_deliveries: dict[str, asyncio.Task[object]] = {}
        self._mcp_client: MainSequenceMCPClient | None = None
        self._mcp_lock = asyncio.Lock()
        self._startup_ready = False
        self._local_state_ready = False
        self._auth_ready = False
        self._provider_control_ready = False
        self._snapshot_restore_count = 0
        self._snapshot_fallback_count = 0
        self._snapshot_upload_count = 0
        self._draining = False
        self._closed = False

    async def start(self) -> None:
        state_ready = getattr(self.backend, "state_ready", None)
        if state_ready is not None:
            self._local_state_ready = bool(await state_ready())
        if self.settings.startup_dependencies_enabled:
            await self.backend.auth.prefetch()
            self._auth_ready = True
            prefetch_provider = getattr(self.backend, "prefetch_provider", None)
            if prefetch_provider is not None:
                await prefetch_provider()
                self._provider_control_ready = True
            await self._get_mcp_client()
        self._startup_ready = True
        if self._eviction_task is None:
            self._eviction_task = asyncio.create_task(
                self._eviction_loop(),
                name="ms-tau-session-eviction",
            )

    def snapshot(self) -> dict[str, object]:
        extension_states = [
            runtime.project_extension_state
            for runtime in self._runtimes.values()
            if runtime.project_extension_state is not None
        ]
        tool_catalog_digests = {
            state.tool_catalog_digest for state in extension_states if state.tool_catalog_digest
        }
        return {
            "mode": "local" if self.settings.local_mode else "managed",
            "workspace_digest": self.settings.workspace_digest,
            "holder_id": self.holder_id,
            "loaded_sessions": len(self._runtimes),
            "working_sessions": sum(
                runtime.runtime_activity == "working" for runtime in self._runtimes.values()
            ),
            "lease_lost_sessions": sum(runtime.lease_lost for runtime in self._runtimes.values()),
            "startup_ready": self._startup_ready,
            "local_store_ready": (self._local_state_ready if self.settings.local_mode else None),
            "mainsequence_auth_ready": self._auth_ready,
            "provider_control_ready": (
                self._provider_control_ready if self.settings.local_mode else None
            ),
            "mcp_connected": self._mcp_client is not None,
            "mcp_tool_count": len(self._mcp_client.tools) if self._mcp_client else 0,
            "mcp_resource_count": len(self._mcp_client.resources) if self._mcp_client else 0,
            "mcp_session_proof_limited_tool_count": (
                sum(
                    (tool.meta or {}).get(CALLER_SESSION_PROOF_REQUIRED_META_KEY) is True
                    for tool in self._mcp_client.tools
                )
                if self.settings.local_mode and self._mcp_client is not None
                else 0
            ),
            "snapshot_restore_count": self._snapshot_restore_count,
            "snapshot_fallback_count": self._snapshot_fallback_count,
            "snapshot_upload_count": self._snapshot_upload_count,
            "project_extensions_enabled": True,
            "loaded_extension_count": max(
                (state.loaded_extension_count for state in extension_states),
                default=0,
            ),
            "project_tool_count": max(
                (state.project_tool_count for state in extension_states),
                default=0,
            ),
            "extension_diagnostic_count": max(
                (state.extension_diagnostic_count for state in extension_states),
                default=0,
            ),
            "extension_error_count": max(
                (state.extension_error_count for state in extension_states),
                default=0,
            ),
            "tool_catalog_digest": (
                next(iter(tool_catalog_digests)) if len(tool_catalog_digests) == 1 else None
            ),
        }

    @property
    def draining(self) -> bool:
        return self._draining or self._closed

    @property
    def deployment_ready(self) -> bool:
        """Whether TAU bootstrap completed and the runtime still accepts work."""

        return self._startup_ready and not self.draining

    async def task_execution_fence(self, session_uid: str) -> RuntimeExecutionFence:
        """Acquire/load the canonical runtime and expose its existing session lease."""

        runtime = await self.get(session_uid)
        if runtime.lease_lost:
            raise LeaseLostError(f"Runtime lease was lost for session {session_uid}")
        return RuntimeExecutionFence(
            holder_id=self.holder_id,
            lease_token=runtime.storage.lease_token,
        )

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
            raise RuntimeError("Main Sequence TAU SDK runtime is shutting down")
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
                supported_snapshot_schema_versions=[SNAPSHOT_SCHEMA_VERSION],
                supported_provider_control_schema_versions=[1],
                tau_runtime_version=TAU_RUNTIME_VERSION,
            ),
        )
        missing_capabilities = {
            name: version
            for name, version in RUNTIME_CAPABILITIES.items()
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
            mcp_client = await self._get_mcp_client()
            cwd = self._resolve_cwd()
            project_extension_state = ProjectExtensionState(enabled=True)
            caller_session_proof = None
            if not self.settings.local_mode:
                caller_session_proof = {
                    "caller_agent_session_uid": session_uid,
                    "lease_holder_id": lease.holder_id,
                    "lease_token": lease.lease_token,
                }
            tools = [
                *create_coding_tools(cwd=cwd),
                *create_mainsequence_mcp_tools(
                    mcp_client,
                    caller_session_proof=caller_session_proof,
                    allow_missing_session_proof=self.settings.local_mode,
                ),
                *create_task_control_tools(),
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
                    append_system_prompt=mainsequence_mcp_resource_prompt(mcp_client),
                    resource_paths=tau_resource_paths(cwd),
                    project_extensions_enabled=True,
                    trust_override="approve",
                )
            )
            project_extension_state.update_from_session(coding_session)
            self._log_project_extension_diagnostics(
                session_uid=session_uid,
                cwd=cwd,
                coding_session=coding_session,
                state=project_extension_state,
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
                provider_control_schema=bootstrap.provider_control.schema_version,
                catalog_digest=bootstrap.provider_control.catalog_digest,
                project_extension_state=project_extension_state,
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
                name=f"ms-tau-lease-{session_uid}",
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
                project_extensions_enabled=project_extension_state.enabled,
                loaded_extension_count=project_extension_state.loaded_extension_count,
                project_tool_count=project_extension_state.project_tool_count,
                extension_diagnostic_count=(project_extension_state.extension_diagnostic_count),
                extension_error_count=project_extension_state.extension_error_count,
                tool_catalog_digest=project_extension_state.tool_catalog_digest,
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

    @staticmethod
    def _log_project_extension_diagnostics(
        *,
        session_uid: str,
        cwd: Path,
        coding_session: CodingSession,
        state: ProjectExtensionState,
    ) -> None:
        diagnostics = coding_session.extension_runtime.diagnostics
        logger.info(
            "runtime.project_extensions.load.completed",
            session_uid=session_uid,
            enabled=state.enabled,
            loaded_extension_count=state.loaded_extension_count,
            project_tool_count=state.project_tool_count,
            tool_catalog_digest=state.tool_catalog_digest,
            diagnostic_count=state.extension_diagnostic_count,
            diagnostic_error_count=state.extension_error_count,
        )
        for diagnostic in diagnostics:
            path = _repository_relative_path(diagnostic.path, cwd=cwd)
            log = logger.error if diagnostic.severity == "error" else logger.warning
            log(
                "runtime.project_extensions.diagnostic",
                session_uid=session_uid,
                extension_name=diagnostic.name,
                extension_path=path,
                diagnostic_kind=diagnostic.kind,
                diagnostic_severity=diagnostic.severity,
                error_type=_extension_diagnostic_type(diagnostic.message),
            )

    def _resolve_cwd(self) -> Path:
        workspace = self.settings.workspace.resolve()
        if not workspace.exists() or not workspace.is_dir():
            raise ValueError(f"MAINSEQUENCE_TAU_WORKSPACE is not a directory: {workspace}")
        return workspace

    async def prompt(
        self,
        session_uid: str,
        content: str,
        *,
        provenance: TurnProvenance | None = None,
        platform_event: PlatformEvent | None = None,
    ) -> AsyncIterator[TauRuntimeEvent]:
        agent_run_uid = str(uuid.uuid4())
        turn_uid = str(uuid.uuid4())
        with bound_contextvars(
            session_uid=session_uid,
            agent_session_uid=session_uid,
            agent_run_uid=agent_run_uid,
            turn_uid=turn_uid,
            agent_uid="ms-tau-sdk",
        ):
            async for event in self._prompt_with_context(
                session_uid,
                content,
                provenance=provenance,
                platform_event=platform_event,
            ):
                yield event

    async def _prompt_with_context(
        self,
        session_uid: str,
        content: str,
        *,
        provenance: TurnProvenance | None = None,
        platform_event: PlatformEvent | None = None,
    ) -> AsyncIterator[TauRuntimeEvent]:
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
        yield TauRuntimeEvent(
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
        yield TauRuntimeEvent(type="lifecycle", data={"phase": "generating"})

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
                        platform_event=platform_event,
                        durability_task=lambda: self.create_background_task(
                            self._settle_turn_durability(runtime, turn_uid),
                            name=f"ms-tau-turn-persist-{session_uid}-{turn_uid}",
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
            name=(f"ms-tau-activity-{runtime.session_uid}-{sequence}-{runtime_activity}"),
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

    def session_turn_active(self, session_uid: str) -> bool:
        runtime = self._runtimes.get(session_uid)
        return bool(
            runtime is not None and (runtime.active_turn_uid is not None or runtime.lock.locked())
        )

    async def persist_platform_event(
        self,
        session_uid: str,
        platform_event: PlatformEvent,
        *,
        idempotency_key: str,
    ) -> bool:
        """Persist one signed control-plane event before acknowledging its delivery."""

        runtime = await self.get(session_uid)
        if runtime.active_turn_uid is not None or runtime.lock.locked():
            raise BackendConflictError(f"Session {session_uid} already has an active turn")
        namespace, payload = platform_event
        async with runtime.lock:
            entries = await runtime.storage.read_all()
            for entry in entries:
                if (
                    getattr(entry, "type", None) == "custom"
                    and getattr(entry, "namespace", None) == namespace
                    and str(getattr(entry, "data", {}).get("deliveryUid") or "")
                    == idempotency_key
                ):
                    return False
            await runtime.coding_session.append_custom_entry(namespace, dict(payload))
            await runtime.storage.flush()
            return True

    def mark_response_delivered(self, session_uid: str) -> bool:
        """Schedule the latest durable snapshot after the transport terminal event."""
        runtime = self._runtimes.get(session_uid)
        if runtime is None or runtime.pending_snapshot_commit is None:
            return False
        commit = runtime.pending_snapshot_commit
        runtime.pending_snapshot_commit = None
        self.create_background_task(
            self._upload_resume_snapshot(runtime, commit),
            name=f"ms-tau-snapshot-upload-{session_uid}-{commit.turn_uid}",
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
            raise RuntimeError("Main Sequence TAU SDK runtime is shutting down")
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

    def create_a2a_task_execution(
        self,
        task_uid: str,
        coroutine: Coroutine[Any, Any, object],
        *,
        name: str,
    ) -> tuple[asyncio.Task[object], bool]:
        """Deduplicate local accelerators; Django's dispatch remains recovery owner."""

        existing = self._a2a_task_executions.get(task_uid)
        if existing is not None and not existing.done():
            coroutine.close()
            return existing, False
        task = self.create_background_task(
            coroutine,
            name=name,
            operation_uid=task_uid,
        )
        self._a2a_task_executions[task_uid] = task

        def discard(completed: asyncio.Task[object]) -> None:
            if self._a2a_task_executions.get(task_uid) is completed:
                self._a2a_task_executions.pop(task_uid, None)

        task.add_done_callback(discard)
        return task, True

    def create_a2a_caller_delivery(
        self,
        delivery_uid: str,
        coroutine: Coroutine[Any, Any, object],
        *,
        name: str,
    ) -> tuple[asyncio.Task[object], bool]:
        existing = self._a2a_caller_deliveries.get(delivery_uid)
        if existing is not None and not existing.done():
            coroutine.close()
            return existing, False
        task = self.create_background_task(
            coroutine,
            name=name,
            operation_uid=delivery_uid,
        )
        self._a2a_caller_deliveries[delivery_uid] = task

        def discard(completed: asyncio.Task[object]) -> None:
            if self._a2a_caller_deliveries.get(delivery_uid) is completed:
                self._a2a_caller_deliveries.pop(delivery_uid, None)

        task.add_done_callback(discard)
        return task, True

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
                        name=f"ms-tau-cancel-evict-{runtime.session_uid}",
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
        if runtime is None or runtime.evicting:
            return
        runtime.evicting = True
        if runtime.lock.locked():
            runtime.cancel()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self.settings.shutdown_grace_seconds):
                    async with runtime.lock:
                        pass
        try:
            async with asyncio.timeout(self.settings.shutdown_grace_seconds):
                await runtime.coding_session.aclose()
        except Exception as error:
            logger.exception(
                "runtime.session.close_failed",
                message="Could not close Tau coding session during runtime eviction",
                session_uid=session_uid,
                error_type=type(error).__name__,
                error_message=str(error),
            )
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
