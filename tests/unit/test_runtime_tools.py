import json

import httpx
from tau_web_access import MemorySearchResultStore

from astro import __version__
from astro.tools.runtime_info import create_runtime_info_tool
from astro.tools.web_access import build_web_tools


async def test_runtime_info_reports_the_active_tau_environment(tmp_path):
    tool = create_runtime_info_tool(
        session_uid="session-1",
        cwd=tmp_path,
        provider="openai",
        model="gpt-5.1",
    )

    result = await tool.execute("call-1", {})

    payload = json.loads(result.text)
    assert tool.name == "runtime_info"
    assert payload["runtime"] == "astro-tau"
    assert payload["runtime_version"] == __version__
    assert payload["session_uid"] == "session-1"
    assert payload["cwd"] == str(tmp_path)
    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-5.1"


async def test_astro_web_adapter_reuses_the_caller_owned_http_client(tmp_path):
    async with httpx.AsyncClient() as client:
        tools = build_web_tools(
            cwd=tmp_path,
            store=MemorySearchResultStore(),
            client=client,
        )

    assert [tool.name for tool in tools] == [
        "web_search",
        "code_search",
        "fetch_content",
        "get_search_content",
    ]
