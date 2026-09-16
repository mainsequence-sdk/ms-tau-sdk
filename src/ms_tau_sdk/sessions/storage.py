"""Tau SessionStorage backed by atomic Django entry batches."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter
from tau_agent.session import SessionEntry, SessionStorage

from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.models import (
    RuntimeActivityPatch,
    RuntimeState,
    SessionEntryBatchAppendRequest,
    SessionEntryBatchAppendResponse,
    SessionEntryBatchItem,
    SessionEntryList,
    TauTurnCommit,
    TauTurnLifecycle,
)
from ms_tau_sdk.errors import BackendConflictError, LeaseLostError

SESSION_ENTRY_ADAPTER: TypeAdapter[SessionEntry] = TypeAdapter(SessionEntry)


@dataclass(frozen=True, slots=True)
class _PendingEntry:
    item: SessionEntryBatchItem
    expected_sequence: int
    canonical_bytes: int


class BackendSessionStorage(SessionStorage):
    def __init__(
        self,
        *,
        backend: MainSequenceClient,
        session_uid: str,
        lease_token: str,
        holder_id: str | None = None,
        initial_entries: list[SessionEntry] | None = None,
        initial_next_sequence: int | None = None,
    ) -> None:
        self.backend = backend
        self.session_uid = session_uid
        self.lease_token = lease_token
        self.holder_id = holder_id
        self._next_sequence = initial_next_sequence
        self._entries = list(initial_entries) if initial_entries is not None else None
        self._pending: deque[_PendingEntry] = deque()
        self._mutation_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._write_task: asyncio.Task[None] | None = None
        self._persistence_error: Exception | None = None
        self._lease_valid = True
        self._turn_lifecycle: TauTurnLifecycle | None = None
        self._last_turn_commit: TauTurnCommit | None = None
        self._defer_pending_until_commit = False

    async def begin_turn(
        self,
        *,
        turn_uid: str,
        activity_sequence: int,
    ) -> RuntimeState:
        async with self._state_lock:
            self._raise_persistence_error()
            if not self._lease_valid:
                raise LeaseLostError(f"Runtime lease was lost for session {self.session_uid}")
            if not self.holder_id:
                raise RuntimeError("Tau turn start requires a runtime lease holder")
            state = await self.backend.patch_runtime_activity(
                self.session_uid,
                RuntimeActivityPatch(
                    holder_id=self.holder_id,
                    lease_token=self.lease_token,
                    activity_sequence=activity_sequence,
                    runtime_activity="working",
                    active_turn_uid=turn_uid,
                ),
            )
            self._defer_pending_until_commit = False
            self._turn_lifecycle = TauTurnLifecycle(
                turn_uid=turn_uid,
                phase="started",
                activity_sequence=activity_sequence,
            )
            return state

    async def commit_turn(
        self,
        *,
        turn_uid: str,
        activity_sequence: int,
    ) -> TauTurnCommit:
        async with self._state_lock:
            self._raise_persistence_error()
            self._defer_pending_until_commit = False
            self._turn_lifecycle = TauTurnLifecycle(
                turn_uid=turn_uid,
                phase="committed",
                activity_sequence=activity_sequence,
            )
            if self._write_task is None and not self._defer_pending_until_commit:
                self._write_task = asyncio.create_task(
                    self._persist_pending(),
                    name=f"ms-tau-session-persist-{self.session_uid}",
                )
        await self.flush()
        if self._last_turn_commit is None or self._last_turn_commit.turn_uid != turn_uid:
            raise BackendConflictError("Backend omitted the Tau turn commit boundary")
        return self._last_turn_commit

    @property
    def next_sequence(self) -> int | None:
        return self._next_sequence

    @property
    def persistence_failed(self) -> bool:
        return self._persistence_error is not None

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

    async def entries_at_sequence(self, sequence: int) -> list[SessionEntry]:
        async with self._state_lock:
            self._raise_persistence_error()
            if self._entries is None:
                await self._load_remote_entries()
            assert self._entries is not None
            if sequence > len(self._entries):
                raise BackendConflictError("Snapshot sequence is ahead of local Tau entries")
            return list(self._entries[:sequence])

    async def append(self, entry: SessionEntry) -> None:
        async with self._mutation_lock:
            await self._queue_entries((entry,))

    async def append_batch(self, entries: Sequence[SessionEntry]) -> None:
        """Persist one Tau transaction through one backend batch request."""
        batch = tuple(entries)
        if not batch:
            return
        async with self._mutation_lock:
            await self.flush()
            await self._queue_entries(batch, require_single_batch=True)
            await self.flush()

    async def _queue_entries(
        self,
        entries: Sequence[SessionEntry],
        *,
        require_single_batch: bool = False,
    ) -> None:
        async with self._state_lock:
            self._raise_persistence_error()
            if not self._lease_valid:
                raise LeaseLostError(f"Runtime lease was lost for session {self.session_uid}")
            if self._entries is None:
                await self._load_remote_entries()
            assert self._entries is not None
            assert self._next_sequence is not None

            pending_entries: list[_PendingEntry] = []
            for offset, entry in enumerate(entries):
                payload = SESSION_ENTRY_ADAPTER.dump_python(
                    entry,
                    mode="json",
                    exclude_none=True,
                )
                canonical_bytes = len(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                )
                pending_entries.append(
                    _PendingEntry(
                        item=SessionEntryBatchItem(
                            idempotency_key=entry.id,
                            entry=payload,
                        ),
                        expected_sequence=self._next_sequence + offset,
                        canonical_bytes=canonical_bytes,
                    )
                )

            if require_single_batch:
                backend_settings = getattr(self.backend, "settings", None)
                maximum_count = getattr(
                    backend_settings,
                    "session_entry_batch_max_entries",
                    100,
                )
                maximum_bytes = getattr(
                    backend_settings,
                    "session_entry_batch_max_bytes",
                    8 * 1024 * 1024,
                )
                if (
                    len(pending_entries) > maximum_count
                    or sum(pending.canonical_bytes for pending in pending_entries) > maximum_bytes
                ):
                    raise ValueError(
                        "Atomic Tau session batch exceeds the configured backend batch limit"
                    )

            self._entries.extend(entries)
            self._next_sequence += len(entries)
            self._pending.extend(pending_entries)
            if self._write_task is None and not self._defer_pending_until_commit:
                self._write_task = asyncio.create_task(
                    self._persist_pending(),
                    name=f"ms-tau-session-persist-{self.session_uid}",
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
        self._set_remote_entries(result)

    def _set_remote_entries(self, result: SessionEntryList) -> None:
        self._entries = [
            SESSION_ENTRY_ADAPTER.validate_python(record.entry_json) for record in result.entries
        ]
        self._next_sequence = result.next_sequence

    def _batch_snapshot(self) -> list[_PendingEntry]:
        backend_settings = getattr(self.backend, "settings", None)
        maximum_count = getattr(backend_settings, "session_entry_batch_max_entries", 100)
        maximum_bytes = getattr(backend_settings, "session_entry_batch_max_bytes", 8 * 1024 * 1024)
        batch: list[_PendingEntry] = []
        aggregate_bytes = 0
        for pending in self._pending:
            if len(batch) >= maximum_count:
                break
            if batch and aggregate_bytes + pending.canonical_bytes > maximum_bytes:
                break
            batch.append(pending)
            aggregate_bytes += pending.canonical_bytes
        return batch

    async def _persist_pending(self) -> None:
        while True:
            async with self._state_lock:
                if not self._pending and self._turn_lifecycle is None:
                    self._write_task = None
                    return
                if self._defer_pending_until_commit and (
                    self._turn_lifecycle is None or self._turn_lifecycle.phase != "committed"
                ):
                    self._write_task = None
                    return
                if not self._lease_valid:
                    self._record_persistence_error(
                        LeaseLostError(f"Runtime lease was lost for session {self.session_uid}")
                    )
                    return
                batch = self._batch_snapshot()
                expected_sequence = batch[0].expected_sequence if batch else self._next_sequence
                if expected_sequence is None:
                    await self._load_remote_entries()
                    expected_sequence = self._next_sequence
                assert expected_sequence is not None
                lifecycle = self._turn_lifecycle
                request = SessionEntryBatchAppendRequest(
                    lease_token=self.lease_token,
                    expected_sequence=expected_sequence,
                    entries=[pending.item for pending in batch],
                    holder_id=self.holder_id if lifecycle is not None else None,
                    turn=lifecycle,
                )

            try:
                response = await self.backend.append_entries(
                    self.session_uid,
                    request,
                )
                self._validate_batch_response(batch, request, response)
            except BackendConflictError as error:
                if await self._recover_exact_replay(batch):
                    continue
                async with self._state_lock:
                    self._record_persistence_error(error)
                return
            except Exception as error:
                async with self._state_lock:
                    self._record_persistence_error(error)
                return

            async with self._state_lock:
                self._acknowledge_batch(batch)
                if response.turn_commit is not None:
                    self._last_turn_commit = response.turn_commit
                if self._turn_lifecycle == lifecycle:
                    self._turn_lifecycle = None
                    if lifecycle is not None and lifecycle.phase == "started":
                        self._defer_pending_until_commit = True

    @staticmethod
    def _validate_batch_response(
        batch: list[_PendingEntry],
        request: SessionEntryBatchAppendRequest,
        response: SessionEntryBatchAppendResponse,
    ) -> None:
        records = response.entries
        expected_next_sequence = (
            batch[-1].expected_sequence + 1 if batch else request.expected_sequence
        )
        expected_created_count = 0 if response.replayed else len(batch)
        if (
            response.next_sequence != expected_next_sequence
            or response.created_count != expected_created_count
        ):
            raise BackendConflictError(
                "Backend returned inconsistent Tau batch metadata",
                detail={
                    "expected_next_sequence": expected_next_sequence,
                    "next_sequence": response.next_sequence,
                    "expected_created_count": expected_created_count,
                    "created_count": response.created_count,
                    "replayed": response.replayed,
                },
            )
        if len(records) != len(batch):
            raise BackendConflictError(
                "Backend returned an unexpected Tau batch length",
                detail={"expected_count": len(batch), "record_count": len(records)},
            )
        for pending, record in zip(batch, records, strict=True):
            if (
                record.sequence != pending.expected_sequence
                or record.idempotency_key != pending.item.idempotency_key
                or record.entry_json != pending.item.entry
            ):
                raise BackendConflictError(
                    "Backend returned a divergent Tau batch acknowledgement",
                    detail={
                        "expected_sequence": pending.expected_sequence,
                        "record_sequence": record.sequence,
                        "idempotency_key": pending.item.idempotency_key,
                    },
                )
        if request.turn is not None and request.turn.phase == "committed":
            commit = response.turn_commit
            if (
                commit is None
                or commit.turn_uid != request.turn.turn_uid
                or commit.next_sequence != expected_next_sequence
            ):
                raise BackendConflictError("Backend returned an inconsistent Tau turn commit")

    def _acknowledge_batch(self, batch: list[_PendingEntry]) -> None:
        for expected in batch:
            actual = self._pending.popleft()
            if actual != expected:
                raise BackendConflictError(
                    "Tau persistence queue changed while a batch was in flight"
                )

    async def _recover_exact_replay(self, batch: list[_PendingEntry]) -> bool:
        if not batch:
            return False
        try:
            durable = await self.backend.get_entries(self.session_uid)
        except Exception:
            return False
        by_sequence = {record.sequence: record for record in durable.entries}
        for pending in batch:
            record = by_sequence.get(pending.expected_sequence)
            if record is None or (
                record.idempotency_key != pending.item.idempotency_key
                or record.entry_json != pending.item.entry
            ):
                return False
        async with self._state_lock:
            self._acknowledge_batch(batch)
            if self._turn_lifecycle is not None and self._turn_lifecycle.phase != "committed":
                self._turn_lifecycle = None
            remaining = list(self._pending)
            self._set_remote_entries(durable)
            assert self._entries is not None
            assert self._next_sequence is not None
            self._entries.extend(
                SESSION_ENTRY_ADAPTER.validate_python(pending.item.entry) for pending in remaining
            )
            self._next_sequence += len(remaining)
        return True

    def _record_persistence_error(self, error: Exception) -> None:
        self._persistence_error = error
        self._write_task = None

    def _raise_persistence_error(self) -> None:
        if self._persistence_error is not None:
            raise self._persistence_error


def serialize_entry(entry: SessionEntry) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        SESSION_ENTRY_ADAPTER.dump_python(entry, mode="json", exclude_none=True),
    )
