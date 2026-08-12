import asyncio

from tau_agent.session import SessionInfoEntry

from astro.backend.models import SessionEntryList, SessionEntryRecord
from astro.errors import BackendConflictError
from astro.sessions.storage import BackendSessionStorage


class FakeBackend:
    def __init__(self):
        self.records = []
        self.requests = []
        self.get_entries_calls = 0

    async def get_entries(self, session_uid):
        assert session_uid == "session-1"
        self.get_entries_calls += 1
        return SessionEntryList(
            entries=list(self.records),
            next_sequence=len(self.records),
        )

    async def append_entry(self, session_uid, request):
        assert session_uid == "session-1"
        self.requests.append(request)
        record = SessionEntryRecord(
            sequence=request.expected_sequence,
            entry_type=request.entry["type"],
            entry_json=request.entry,
            idempotency_key=request.idempotency_key,
        )
        self.records.append(record)
        return record


async def test_storage_appends_native_tau_entries_with_expected_sequence():
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

    assert [request.expected_sequence for request in backend.requests] == [0, 1]
    assert [request.idempotency_key for request in backend.requests] == [
        first.id,
        second.id,
    ]
    assert [entry.id for entry in restored] == [first.id, second.id]
    assert backend.get_entries_calls == 1


async def test_storage_writes_behind_and_flushes_at_the_durability_boundary():
    write_started = asyncio.Event()
    release_write = asyncio.Event()

    class BlockingBackend(FakeBackend):
        async def append_entry(self, session_uid, request):
            write_started.set()
            await release_write.wait()
            return await super().append_entry(session_uid, request)

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
    assert backend.requests[0].idempotency_key == entry.id


async def test_storage_rereads_backend_after_sequence_conflict():
    class ConflictingBackend(FakeBackend):
        async def append_entry(self, session_uid, request):
            raise BackendConflictError("sequence conflict")

    backend = ConflictingBackend()
    storage = BackendSessionStorage(
        backend=backend,
        session_uid="session-1",
        lease_token="lease-token",
    )

    await storage.append(SessionInfoEntry(cwd="/workspace"))

    try:
        await storage.flush()
    except BackendConflictError:
        pass
    else:
        raise AssertionError("flush must surface the backend sequence conflict")

    assert backend.get_entries_calls == 2
