"""One lease-owned loaded Tau coding session."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from tau_coding import CodingSession

from astro.backend.mcp import MainSequenceMCPClient
from astro.runtime.events import AstroRuntimeEvent, translate_tau_event
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
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used_at: float = field(default_factory=time.monotonic)
    lease_lost: bool = False
    evicting: bool = False
    cancellation_requested: bool = False

    async def prompt(self, content: str) -> AsyncIterator[AstroRuntimeEvent]:
        if self.evicting:
            raise RuntimeError("Session runtime is being evicted")
        if self.cancellation_requested:
            raise RuntimeError("Session runtime cancellation has been requested")
        async with self.lock:
            self.last_used_at = time.monotonic()
            settled_event: AstroRuntimeEvent | None = None
            async for event in self.coding_session.prompt(content):
                translated = translate_tau_event(event)
                if translated.type == "agent_settled":
                    settled_event = translated
                else:
                    yield translated
            await self.storage.flush()
            if settled_event is not None:
                yield settled_event
            self.last_used_at = time.monotonic()

    def cancel(self, *, force: bool = False) -> bool:
        if not force and not self.coding_session.is_running:
            return False
        self.coding_session.cancel()
        return True
