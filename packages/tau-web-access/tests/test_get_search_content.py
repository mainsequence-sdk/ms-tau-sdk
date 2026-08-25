import httpx
import pytest

from tau_web_access import MemorySearchResultStore, WebAccessSettings
from tau_web_access.models import QueryResult, SearchResult, StoredSearchData
from tau_web_access.tools import WebToolSet


@pytest.mark.asyncio
async def test_get_search_content_preserves_query_index_contract(tmp_path):
    store = MemorySearchResultStore()
    await store.put(
        StoredSearchData(
            id="search-id",
            timestamp_ms=1,
            queries=[
                QueryResult(
                    query="tau python",
                    answer="Tau is Python.",
                    results=[SearchResult(title="Tau", url="https://example.com")],
                )
            ],
        )
    )
    async with httpx.AsyncClient() as client:
        tool_set = WebToolSet(
            settings=WebAccessSettings(allow_private_networks=True),
            store=store,
            client=client,
            cwd=tmp_path,
        )

        result = await tool_set.get_search_content_tool().execute(
            "get-1",
            {"responseId": "search-id", "queryIndex": 0},
        )

    assert '## Results for: "tau python"' in result.text
    assert result.details["resultCount"] == 1
