"""Tau SessionStorage backed by append-only Django records."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter
from tau_agent.session import SessionEntry, SessionStorage

from astro.backend.client import MainSequenceClient
from astro.backend.models import SessionEntryAppendRequest
from astro.errors import BackendConflictError, LeaseLostError

SESSION_ENTRY_ADAPTER: TypeAdapter[SessionEntry] = TypeAdapter(SessionEntry)


@dataclass(frozen=True, slots=True)
class _PendingEntry:
    request: SessionEntryAppendRequest


class BackendSessionStorage(SessionStorage):
    def __init__(
        self,
        *,
        backend: MainSequenceClient,
        session_uid: str,
        lease_token: str,
    ) -> None:
        self.backend = backend
        self.session_uid = session_uid
        self.lease_token = lease_token
        self._next_sequence: int | None = None
        self._entries: list[SessionEntry] | None = None
        self._pending: deque[_PendingEntry] = deque()
        self._state_lock = asyncio.Lock()
        self._write_task: asyncio.Task[None] | None = None
        self._persistence_error: Exception | None = None
        self._lease_valid = True

    @property
    def next_sequence(self) -> int | None:
        return self._next_sequence

    def replace_lease_token(self, lease_token: str) -> None:
        self.lease_token = lease_token
        self._lease_valid = True

    def invalidate_lease(self) -> None:
        self._lease_valid = False

    async def read_all(self) -> list[SessionEntry]:
        async with self._state_lock:
            self._raise_persistence_error()
            if self._entries is None:
                await self._load_remote_entries()
            assert self._entries is not None
            return list(self._entries)

    async def append(self, entry: SessionEntry) -> None:
        async with self._state_lock:
            self._raise_persistence_error()
            if not self._lease_valid:
                raise LeaseLostError(f"Runtime lease was lost for session {self.session_uid}")
            if self._entries is None:
                await self._load_remote_entries()
            assert self._entries is not None
            assert self._next_sequence is not None

            payload = SESSION_ENTRY_ADAPTER.dump_python(
                entry,
                mode="json",
                exclude_none=True,
            )
            expected_sequence = self._next_sequence
            self._entries.append(entry)
            self._next_sequence += 1
            self._pending.append(
                _PendingEntry(
                    request=SessionEntryAppendRequest(
                        lease_token=self.lease_token,
                        expected_sequence=expected_sequence,
                        idempotency_key=entry.id,
                        entry=payload,
                    ),
                )
            )
            if self._write_task is None:
                self._write_task = asyncio.create_task(
                    self._persist_pending(),
                    name=f"astro-session-persist-{self.session_uid}",
                )

    async def flush(self) -> None:
        """Wait until every queued entry is durable in Django."""

        while True:
            async with self._state_lock:
                self._raise_persistence_error()
                task = self._write_task
                if task is None:
                    return
            await asyncio.shield(task)

    async def resync(self) -> list[SessionEntry]:
        """Explicitly replace the local cache from durable backend state."""

        await self.flush()
        async with self._state_lock:
            await self._load_remote_entries()
            assert self._entries is not None
            return list(self._entries)

    async def _load_remote_entries(self) -> None:
        result = await self.backend.get_entries(self.session_uid)
        entries = [
            SESSION_ENTRY_ADAPTER.validate_python(record.entry_json) for record in result.entries
        ]
        self._entries = entries
        self._next_sequence = result.next_sequence

    async def _persist_pending(self) -> None:
        while True:
            async with self._state_lock:
                if not self._pending:
                    self._write_task = None
                    return
                pending = self._pending.popleft()
                if not self._lease_valid:
                    error = LeaseLostError(f"Runtime lease was lost for session {self.session_uid}")
                    self._record_persistence_error(error)
                    return

            try:
                record = await self.backend.append_entry(
                    self.session_uid,
                    pending.request.model_copy(update={"lease_token": self.lease_token}),
                )
                if record.sequence != pending.request.expected_sequence:
                    raise BackendConflictError(
                        "Backend returned an unexpected Tau entry sequence",
                        detail={
                            "expected_sequence": pending.request.expected_sequence,
                            "record_sequence": record.sequence,
                        },
                    )
            except Exception as error:
                async with self._state_lock:
                    self._record_persistence_error(error)
                if isinstance(error, BackendConflictError):
                    await self._reload_after_conflict()
                return

    def _record_persistence_error(self, error: Exception) -> None:
        self._persistence_error = error
        self._pending.clear()
        self._write_task = None

    async def _reload_after_conflict(self) -> None:
        try:
            result = await self.backend.get_entries(self.session_uid)
            entries = [
                SESSION_ENTRY_ADAPTER.validate_python(record.entry_json)
                for record in result.entries
            ]
        except Exception:
            return
        async with self._state_lock:
            self._entries = entries
            self._next_sequence = result.next_sequence

    def _raise_persistence_error(self) -> None:
        if self._persistence_error is not None:
            raise self._persistence_error


def serialize_entry(entry: SessionEntry) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        SESSION_ENTRY_ADAPTER.dump_python(entry, mode="json", exclude_none=True),
    )
