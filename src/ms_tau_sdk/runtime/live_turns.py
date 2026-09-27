"""In-memory view of the Tau turn a local session is running.

Tau keeps a running turn's entries in memory until the turn commits, so the
durable transcript cannot show it. A ``LiveTurn`` follows the turn's runtime
events instead and renders it as Tau-shaped entries: the provenance stamp, the
user prompt, every message Tau has completed, and the assistant message it is
still streaming. Chat history appends those entries to the durable branch until
the turn commits.

A local chat turn also outlives the request that started it. ``LocalChatTurn``
hands the turn's events to that request while it stays connected.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from ms_tau_sdk.runtime.events import TauRuntimeEvent
from ms_tau_sdk.runtime.provenance import PROVENANCE_NAMESPACE

_TURN_END = object()


@dataclass(slots=True, eq=False)
class LiveTurn:
    """The running turn of one session, projected from its runtime events."""

    session_uid: str
    turn_uid: str
    prompt: str
    provenance: Mapping[str, str] | None = None
    started_at: float = field(default_factory=time.time)
    messages: list[dict[str, Any]] = field(default_factory=list)
    partial: dict[str, Any] | None = None

    def observe(self, event: TauRuntimeEvent) -> None:
        """Record one completed message or the latest partial assistant message."""

        message = event.data.get("message")
        if event.type == "message_end":
            if isinstance(message, dict):
                self.messages.append(message)
                if message.get("role") == "assistant":
                    self.partial = None
            return
        if event.type == "message_start":
            if isinstance(message, dict) and message.get("role") == "assistant":
                self.partial = message
            return
        partial = event.data.get("partial")
        if isinstance(partial, dict) and partial.get("role") == "assistant":
            self.partial = partial

    def entries(self) -> list[dict[str, Any]]:
        """Return the turn as Tau-shaped entries in transcript order."""

        entries: list[dict[str, Any]] = []
        if self.provenance:
            entries.append(
                {
                    "type": "custom",
                    "namespace": PROVENANCE_NAMESPACE,
                    "data": dict(self.provenance),
                    "timestamp": self.started_at,
                }
            )
        entries.extend({"type": "message", "message": message} for message in self.messages)
        if not any(message.get("role") == "user" for message in self.messages):
            # Tau reports the stored prompt only once the session has loaded.
            entries.append(
                {
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": self.prompt,
                        "timestamp": int(self.started_at * 1000),
                    },
                }
            )
        if self.partial is not None:
            entries.append({"type": "message", "message": self.partial})
        return entries


class LocalChatTurn:
    """The event feed of a detached local chat turn for the request that started it.

    The turn runs in a task the runtime manager owns, so a client disconnect does
    not cancel it. Events reach the request only while it stays attached; a
    detached turn drops them and still runs to its durable end.
    """

    def __init__(self, session_uid: str) -> None:
        self.session_uid = session_uid
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._attached = True

    @property
    def attached(self) -> bool:
        return self._attached

    def publish(self, event: TauRuntimeEvent) -> None:
        if self._attached:
            self._queue.put_nowait(event)

    def fail(self, error: BaseException) -> None:
        if self._attached:
            self._queue.put_nowait(error)

    def finish(self) -> None:
        if self._attached:
            self._queue.put_nowait(_TURN_END)

    def detach(self) -> None:
        """Stop delivering events; the turn itself keeps running."""

        self._attached = False
        while not self._queue.empty():
            self._queue.get_nowait()

    async def events(self) -> AsyncIterator[TauRuntimeEvent]:
        """Yield the turn's events, re-raising the error that ended it, if any."""

        while True:
            item = await self._queue.get()
            if item is _TURN_END:
                return
            if isinstance(item, BaseException):
                raise item
            assert isinstance(item, TauRuntimeEvent)
            yield item
