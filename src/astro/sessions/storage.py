"""Tau SessionStorage backed by append-only Django records."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from pydantic import TypeAdapter
from tau_agent.session import SessionEntry, SessionStorage

from astro.backend.client import MainSequenceClient
from astro.backend.models import SessionEntryAppendRequest
from astro.errors import LeaseLostError

SESSION_ENTRY_ADAPTER: TypeAdapter[SessionEntry] = TypeAdapter(SessionEntry)


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
        self._append_lock = asyncio.Lock()
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
        result = await self.backend.get_entries(self.session_uid)
        self._next_sequence = result.next_sequence
        return [
            SESSION_ENTRY_ADAPTER.validate_python(record.entry_json)
            for record in result.entries
        ]

    async def append(self, entry: SessionEntry) -> None:
        if not self._lease_valid:
            raise LeaseLostError(f"Runtime lease was lost for session {self.session_uid}")
        async with self._append_lock:
            if self._next_sequence is None:
                await self.read_all()
            assert self._next_sequence is not None
            payload = SESSION_ENTRY_ADAPTER.dump_python(
                entry,
                mode="json",
                exclude_none=True,
            )
            record = await self.backend.append_entry(
                self.session_uid,
                SessionEntryAppendRequest(
                    lease_token=self.lease_token,
                    expected_sequence=self._next_sequence,
                    idempotency_key=entry.id,
                    entry=payload,
                ),
            )
            self._next_sequence = max(self._next_sequence, record.sequence + 1)


def serialize_entry(entry: SessionEntry) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        SESSION_ENTRY_ADAPTER.dump_python(entry, mode="json", exclude_none=True),
    )
