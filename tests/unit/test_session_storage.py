from tau_agent.session import SessionInfoEntry

from astro.backend.models import SessionEntryList, SessionEntryRecord
from astro.sessions.storage import BackendSessionStorage


class FakeBackend:
    def __init__(self):
        self.records = []
        self.requests = []

    async def get_entries(self, session_uid):
        assert session_uid == "session-1"
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

    assert [request.expected_sequence for request in backend.requests] == [0, 1]
    assert [request.idempotency_key for request in backend.requests] == [
        first.id,
        second.id,
    ]
    assert [entry.id for entry in restored] == [first.id, second.id]
