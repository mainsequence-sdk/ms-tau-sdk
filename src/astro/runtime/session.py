"""One lease-owned loaded Tau coding session."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from tau_coding import CodingSession

from astro.backend.mcp import MainSequenceMCPClient
from astro.backend.models import TauTurnCommit
from astro.runtime.events import AstroRuntimeEvent, translate_tau_event
from astro.runtime.extensions import ProjectExtensionState
from astro.runtime.provenance import PROVENANCE_NAMESPACE, TurnProvenance
from astro.sessions.storage import BackendSessionStorage


@dataclass(slots=True)
class ActiveSessionRuntime:
    session_uid: str
    holder_id: str
    coding_session: CodingSession
    storage: BackendSessionStorage
    provider: object
    provider_name: str = ""
    model: str = ""
    mcp_client: MainSequenceMCPClient | None = None
    lease_renew_task: asyncio.Task[None] | None = None
    persistence_task: asyncio.Task[object] | None = None
    pending_snapshot_commit: TauTurnCommit | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used_at: float = field(default_factory=time.monotonic)
    lease_lost: bool = False
    evicting: bool = False
    cancellation_requested: bool = False
    runtime_activity: str = "loading"
    activity_revision: int = 0
    activity_sequence: int = 0
    active_turn_uid: str | None = None
    runtime_config_sha256: str = ""
    capability_set_sha256: str = ""
    provider_control_schema: int = 0
    catalog_digest: str = ""
    project_extension_state: ProjectExtensionState | None = None

    async def prompt(
        self,
        content: str,
        *,
        durability_task: Callable[[], asyncio.Task[object]],
        provenance: TurnProvenance | None = None,
    ) -> AsyncIterator[AstroRuntimeEvent]:
        if self.evicting:
            raise RuntimeError("Session runtime is being evicted")
        if self.cancellation_requested:
            raise RuntimeError("Session runtime cancellation has been requested")
        if self.persistence_task is not None:
            await asyncio.shield(self.persistence_task)
        async with self.lock:
            self.last_used_at = time.monotonic()
            settled_event: AstroRuntimeEvent | None = None
            if provenance:
                # Stamp the turn with a tau-native custom entry: it is part of
                # the canonical entry contract, stays out of the model context,
                # reloads as a first-class entry, and rides the turn's commit
                # batch, so the send path gains no request.
                await self.coding_session.append_custom_entry(
                    PROVENANCE_NAMESPACE, dict(provenance)
                )
            async for event in self.coding_session.prompt(content):
                translated = translate_tau_event(event)
                if settled_event is not None and translated.type != "agent_settled":
                    raise RuntimeError("Tau emitted model or tool activity after output completion")
                if translated.type == "agent_settled":
                    settled_event = translated
                else:
                    yield translated
            task = durability_task()
            self.persistence_task = task
            yield settled_event or AstroRuntimeEvent(type="agent_settled")
            yield AstroRuntimeEvent(type="lifecycle", data={"phase": "saving"})
            await asyncio.shield(task)
            yield AstroRuntimeEvent(type="lifecycle", data={"phase": "durable"})
            yield AstroRuntimeEvent(type="persistence_settled")
            self.last_used_at = time.monotonic()

    def cancel(self, *, force: bool = False) -> bool:
        if not force and not self.coding_session.is_running:
            return False
        self.coding_session.cancel()
        return True
