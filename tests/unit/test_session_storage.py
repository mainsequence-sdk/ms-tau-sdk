import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from tau_agent.session import SessionInfoEntry

from astro.backend.models import (
    RuntimeState,
    SessionEntryBatchAppendResponse,
    SessionEntryList,
    SessionEntryRecord,
    TauTurnCommit,
)
from astro.errors import BackendConflictError
from astro.sessions.storage import BackendSessionStorage


class FakeBackend:
    def __init__(self):
        self.records = []
        self.requests = []
        self.get_entries_calls = 0
        self.settings = SimpleNamespace(
            session_entry_batch_max_entries=100,
            session_entry_batch_max_bytes=8 * 1024 * 1024,
        )

    async def get_entries(self, session_uid):
        assert session_uid == "session-1"
        self.get_entries_calls += 1
        return SessionEntryList(
            entries=list(self.records),
            next_sequence=len(self.records),
        )

    async def patch_runtime_activity(self, session_uid, request):
        assert session_uid == "session-1"
        return RuntimeState(
            harness="tau",
            harness_protocol="tau-session-v1",
            harness_version="0.3.1",
            runtime_activity=request.runtime_activity,
            active_turn_uid=request.active_turn_uid,
            activity_sequence=request.activity_sequence,
            applied=True,
        )

    async def append_entries(self, session_uid, request):
        assert session_uid == "session-1"
        self.requests.append(request)
        records = [
            SessionEntryRecord(
                sequence=request.expected_sequence + offset,
                entry_type=item.entry["type"],
                entry_json=item.entry,
                idempotency_key=item.idempotency_key,
            )
            for offset, item in enumerate(request.entries)
        ]
        self.records.extend(records)
        turn_commit = None
        if request.turn is not None and request.turn.phase == "committed":
            turn_commit = TauTurnCommit(
                turn_uid=request.turn.turn_uid,
                next_sequence=request.expected_sequence + len(records),
                committed_at=datetime.now(UTC),
            )
        return SessionEntryBatchAppendResponse(
            entries=records,
            next_sequence=request.expected_sequence + len(records),
            created_count=len(records),
            replayed=False,
            turn_commit=turn_commit,
        )


async def test_storage_batches_native_tau_entries_with_expected_sequence():
    backend = FakeBackend()
    storage = BackendSessionStorage(
        backend=backend,
        session_uid="session-1",
        lease_token="lease-token",
    )
    first = SessionInfoEntry(cwd="/workspace")
    second = SessionInfoEntry(cwd="/workspace/two")

    await storage.append(first)
    await storage.append(second)
    restored = await storage.read_all()
    await storage.flush()

    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.expected_sequence == 0
    assert [item.idempotency_key for item in request.entries] == [
        first.id,
        second.id,
    ]
    assert [entry.id for entry in restored] == [first.id, second.id]
    assert backend.get_entries_calls == 1


async def test_storage_writes_behind_and_flushes_at_the_durability_boundary():
    write_started = asyncio.Event()
    release_write = asyncio.Event()

    class BlockingBackend(FakeBackend):
        async def append_entries(self, session_uid, request):
            write_started.set()
            await release_write.wait()
            return await super().append_entries(session_uid, request)

    backend = BlockingBackend()
    storage = BackendSessionStorage(
        backend=backend,
        session_uid="session-1",
        lease_token="lease-token",
    )
    entry = SessionInfoEntry(cwd="/workspace")

    await storage.append(entry)
    await write_started.wait()
    restored = await storage.read_all()
    flush_task = asyncio.create_task(storage.flush())
    await asyncio.sleep(0)

    assert [item.id for item in restored] == [entry.id]
    assert backend.get_entries_calls == 1
    assert not flush_task.done()

    release_write.set()
    await flush_task
    assert backend.requests[0].entries[0].idempotency_key == entry.id


async def test_storage_recovers_exact_replay_after_ambiguous_batch_conflict():
    class AmbiguousBackend(FakeBackend):
        async def append_entries(self, session_uid, request):
            await super().append_entries(session_uid, request)
            raise BackendConflictError("response was lost after commit")

    backend = AmbiguousBackend()
    storage = BackendSessionStorage(
        backend=backend,
        session_uid="session-1",
        lease_token="lease-token",
    )
    entry = SessionInfoEntry(cwd="/workspace")

    await storage.append(entry)
    await storage.flush()

    assert len(backend.records) == 1
    assert backend.get_entries_calls == 2
    assert [item.id for item in await storage.read_all()] == [entry.id]


async def test_storage_surfaces_divergent_sequence_conflict_and_gates_reads():
    class ConflictingBackend(FakeBackend):
        async def append_entries(self, session_uid, request):
            raise BackendConflictError("sequence conflict")

    backend = ConflictingBackend()
    storage = BackendSessionStorage(
        backend=backend,
        session_uid="session-1",
        lease_token="lease-token",
    )

    await storage.append(SessionInfoEntry(cwd="/workspace"))

    with pytest.raises(BackendConflictError):
        await storage.flush()
    with pytest.raises(BackendConflictError):
        await storage.read_all()
    assert backend.get_entries_calls == 2


async def test_storage_rejects_inconsistent_batch_response_metadata():
    class InconsistentBackend(FakeBackend):
        async def append_entries(self, session_uid, request):
            response = await super().append_entries(session_uid, request)
            self.records.clear()
            return response.model_copy(update={"next_sequence": 99})

    backend = InconsistentBackend()
    storage = BackendSessionStorage(
        backend=backend,
        session_uid="session-1",
        lease_token="lease-token",
    )

    await storage.append(SessionInfoEntry(cwd="/workspace"))

    with pytest.raises(
        BackendConflictError,
        match="inconsistent Tau batch metadata",
    ):
        await storage.flush()


async def test_entries_appended_during_inflight_batch_use_the_next_batch():
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class BlockingFirstBackend(FakeBackend):
        async def append_entries(self, session_uid, request):
            if not self.requests:
                first_started.set()
                await release_first.wait()
            return await super().append_entries(session_uid, request)

    backend = BlockingFirstBackend()
    storage = BackendSessionStorage(
        backend=backend,
        session_uid="session-1",
        lease_token="lease-token",
    )
    first = SessionInfoEntry(cwd="/workspace")
    second = SessionInfoEntry(cwd="/workspace/two")

    await storage.append(first)
    await first_started.wait()
    await storage.append(second)
    release_first.set()
    await storage.flush()

    assert len(backend.requests) == 2
    assert backend.requests[0].expected_sequence == 0
    assert backend.requests[1].expected_sequence == 1


async def test_storage_combines_pending_output_and_turn_commit():
    backend = FakeBackend()
    storage = BackendSessionStorage(
        backend=backend,
        session_uid="session-1",
        lease_token="lease-token",
        holder_id="astro-holder",
        initial_entries=[],
        initial_next_sequence=0,
    )
    entry = SessionInfoEntry(cwd="/workspace")

    await storage.begin_turn(turn_uid="turn-1", activity_sequence=1)
    await storage.append(entry)
    commit = await storage.commit_turn(turn_uid="turn-1", activity_sequence=2)

    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.holder_id == "astro-holder"
    assert request.turn is not None
    assert request.turn.phase == "committed"
    assert request.turn.activity_sequence == 2
    assert request.entries[0].idempotency_key == entry.id
    assert commit.turn_uid == "turn-1"
    assert commit.next_sequence == 1


async def test_storage_uses_empty_committed_fallback_when_output_is_already_durable():
    backend = FakeBackend()
    storage = BackendSessionStorage(
        backend=backend,
        session_uid="session-1",
        lease_token="lease-token",
        holder_id="astro-holder",
        initial_entries=[],
        initial_next_sequence=0,
    )

    await storage.begin_turn(turn_uid="turn-1", activity_sequence=1)
    commit = await storage.commit_turn(turn_uid="turn-1", activity_sequence=2)

    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.entries == []
    assert request.turn is not None and request.turn.phase == "committed"
    assert commit.next_sequence == 0


async def test_storage_defers_output_after_started_until_atomic_commit():
    started_persisted = asyncio.Event()

    class ObservedBackend(FakeBackend):
        async def append_entries(self, session_uid, request):
            response = await super().append_entries(session_uid, request)
            if request.turn is not None and request.turn.phase == "started":
                started_persisted.set()
            return response

    backend = ObservedBackend()
    storage = BackendSessionStorage(
        backend=backend,
        session_uid="session-1",
        lease_token="lease-token",
        holder_id="astro-holder",
        initial_entries=[],
        initial_next_sequence=0,
    )
    user_entry = SessionInfoEntry(cwd="/workspace/input")
    output_entry = SessionInfoEntry(cwd="/workspace/output")

    await storage.begin_turn(turn_uid="turn-1", activity_sequence=1)
    await storage.append(user_entry)
    await started_persisted.wait()
    await asyncio.sleep(0)
    await storage.append(output_entry)
    await asyncio.sleep(0)

    assert len(backend.requests) == 1
    assert backend.requests[0].turn is not None
    assert backend.requests[0].turn.phase == "started"

    commit = await storage.commit_turn(turn_uid="turn-1", activity_sequence=2)

    assert len(backend.requests) == 2
    committed = backend.requests[1]
    assert committed.turn is not None and committed.turn.phase == "committed"
    assert [item.idempotency_key for item in committed.entries] == [output_entry.id]
    assert commit.next_sequence == 2
