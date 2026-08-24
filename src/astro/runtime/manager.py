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
    RuntimeLeaseReleaseRequest,
    RuntimeLeaseRenewRequest,
    RuntimeLeaseRequest,
)
from astro.capabilities import materialize_session_capabilities
from astro.errors import ConfigurationError, LeaseLostError
from astro.logging import conversation_log_fields
from astro.providers.factory import ProviderFactory
from astro.resources.loader import append_system_prompt, resource_root
from astro.runtime.events import AstroRuntimeEvent
from astro.runtime.observability import TauTurnObserver
from astro.sessions.storage import BackendSessionStorage
from astro.settings import Settings
from astro.tools.mainsequence_mcp import (
    create_mainsequence_mcp_tools,
    mainsequence_mcp_resource_prompt,
)
from astro.tools.runtime_info import create_runtime_info_tool
from astro.tools.web_access import build_web_tools

from .session import ActiveSessionRuntime

logger = structlog.get_logger(__name__)


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
        self._draining = False
        self._closed = False

    async def start(self) -> None:
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
                runtime.coding_session.is_running for runtime in self._runtimes.values()
            ),
            "lease_lost_sessions": sum(runtime.lease_lost for runtime in self._runtimes.values()),
        }

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
        with bound_contextvars(session_uid=session_uid):
            return await self._load_with_context(session_uid)

    async def _load_with_context(
        self,
        session_uid: str,
    ) -> ActiveSessionRuntime:
        load_started_at = time.monotonic()
        logger.info(
            "runtime.session.load.started",
            message="Loading Tau session runtime",
            session_uid=session_uid,
            holder_id=self.holder_id,
        )
        session = await self.backend.get_session(session_uid)
        if session.harness != "tau":
            raise ConfigurationError(
                f"Session {session_uid} uses harness {session.harness!r}; "
                "this Tau runtime can only resume Tau sessions"
            )
        lease = await self.backend.acquire_runtime_lease(
            session_uid,
            RuntimeLeaseRequest(
                holder_id=self.holder_id,
                ttl_seconds=self.settings.runtime_lease_ttl_seconds,
            ),
        )
        logger.info(
            "runtime.lease.acquired",
            message="Acquired backend runtime lease",
            session_uid=session_uid,
            holder_id=self.holder_id,
            checkpoint_version=lease.checkpoint_version,
            lease_expires_at=lease.lease_expires_at,
        )
        storage = BackendSessionStorage(
            backend=self.backend,
            session_uid=session_uid,
            lease_token=lease.lease_token,
        )
        mcp_client: MainSequenceMCPClient | None = None
        try:
            cwd = self._resolve_cwd()
            try:
                async with asyncio.TaskGroup() as startup_tasks:
                    provider_runtime_task = startup_tasks.create_task(
                        self.providers.for_session(
                            session,
                            holder_id=self.holder_id,
                        ),
                        name=f"astro-provider-load-{session_uid}",
                    )
                    capability_task = startup_tasks.create_task(
                        materialize_session_capabilities(
                            backend=self.backend,
                            session_uid=session_uid,
                            asset_root=self.settings.session_asset_root,
                        ),
                        name=f"astro-capabilities-load-{session_uid}",
                    )

                    async def connect_mcp() -> MainSequenceMCPClient:
                        nonlocal mcp_client
                        mcp_client = await MainSequenceMCPClient.connect(
                            settings=self.settings,
                            auth=self.backend.auth,
                        )
                        return mcp_client

                    mcp_task = startup_tasks.create_task(
                        connect_mcp(),
                        name=f"astro-mcp-load-{session_uid}",
                    )
                    startup_tasks.create_task(
                        storage.read_all(),
                        name=f"astro-history-load-{session_uid}",
                    )
            except ExceptionGroup as errors:
                raise errors.exceptions[0] from errors
            provider_runtime = provider_runtime_task.result()
            session_agents_root = capability_task.result()
            mcp_client = mcp_task.result()
            mcp_tools = create_mainsequence_mcp_tools(mcp_client)
            mcp_resource_prompt = mainsequence_mcp_resource_prompt(mcp_client)
            web_store = MemorySearchResultStore()
            tools = [
                *create_coding_tools(cwd=cwd),
                *create_file_tools(cwd=cwd),
                *build_web_tools(cwd=cwd, store=web_store, client=self._web_client),
                *mcp_tools,
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
                        extra_context=mcp_resource_prompt,
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
            )
            runtime.lease_renew_task = asyncio.create_task(
                self._renew_lease(runtime),
                name=f"astro-lease-{session_uid}",
            )
            self._runtimes[session_uid] = runtime
            logger.info(
                "runtime.session.load.completed",
                message="Loaded Tau session runtime",
                session_uid=session_uid,
                provider=provider_runtime.name,
                model=provider_runtime.model,
                mcp_tool_count=len(mcp_client.tools),
                mcp_resource_count=len(mcp_client.resources),
                duration_ms=round(
                    (time.monotonic() - load_started_at) * 1000,
                    3,
                ),
            )
            return runtime
        except BaseException as error:
            if mcp_client is not None:
                with contextlib.suppress(Exception):
                    await mcp_client.aclose()
            with contextlib.suppress(Exception):
                await self.backend.release_runtime_lease(
                    session_uid,
                    RuntimeLeaseReleaseRequest(
                        lease_token=lease.lease_token,
                        holder_id=self.holder_id,
                        reason="runtime_load_failed",
                    ),
                )
            if isinstance(error, asyncio.CancelledError):
                logger.warning(
                    "runtime.session.load.cancelled",
                    message="Tau session runtime load was cancelled",
                    session_uid=session_uid,
                )
            else:
                logger.exception(
                    "runtime.session.load.failed",
                    message="Failed to load Tau session runtime",
                    session_uid=session_uid,
                    error_type=type(error).__name__,
                    error_message=str(error),
                    duration_ms=round(
                        (time.monotonic() - load_started_at) * 1000,
                        3,
                    ),
                )
            raise

    def _resolve_cwd(self) -> Path:
        project_root = self.settings.project_root.resolve()
        if not project_root.exists() or not project_root.is_dir():
            raise ValueError(f"ASTRO_PROJECT_CWD does not exist: {project_root}")
        return project_root

    async def prompt(
        self,
        session_uid: str,
        content: str,
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
            async for event in self._prompt_with_context(session_uid, content):
                yield event

    async def _prompt_with_context(
        self,
        session_uid: str,
        content: str,
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
        runtime = await self.get(session_uid)
        if runtime.lease_lost:
            raise LeaseLostError(f"Runtime lease was lost for session {session_uid}")
        if runtime.cancellation_requested:
            raise LeaseLostError(f"Runtime cancellation was requested for session {session_uid}")
        started_at = time.monotonic()
        terminal_status = "completed"
        error_type: str | None = None
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
                    async for event in runtime.prompt(content):
                        observer.observe(event)
                        yield event
            except TimeoutError:
                terminal_status = "timed_out"
                error_type = "TimeoutError"
                runtime.cancel(force=True)
                raise
        except asyncio.CancelledError:
            terminal_status = "cancelled"
            error_type = "CancelledError"
            raise
        except Exception as error:
            if terminal_status == "completed":
                terminal_status = "failed"
            error_type = type(error).__name__
            raise
        finally:
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

    async def cancel(self, session_uid: str) -> bool:
        runtime = self._runtimes.get(session_uid)
        if runtime is None:
            return False
        runtime.cancellation_requested = True
        cancelled = runtime.cancel()
        return cancelled

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
                "project_uid",
                "organization_project_environment_uid",
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
            if runtime.mcp_client is not None:
                with contextlib.suppress(Exception):
                    await runtime.mcp_client.aclose()
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
        if runtime.mcp_client is not None:
            with contextlib.suppress(Exception):
                await runtime.mcp_client.aclose()
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
        await self._web_client.aclose()
