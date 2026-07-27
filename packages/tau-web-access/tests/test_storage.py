import pytest

from tau_web_access.models import StoredFetchData
from tau_web_access.storage import MemorySearchResultStore


@pytest.mark.asyncio
async def test_memory_store_is_bounded():
    store = MemorySearchResultStore(max_entries=1)
    await store.put(StoredFetchData(id="first", timestamp_ms=1, urls=[]))
    await store.put(StoredFetchData(id="second", timestamp_ms=2, urls=[]))

    assert await store.get("first") is None
    assert (await store.get("second")).id == "second"
