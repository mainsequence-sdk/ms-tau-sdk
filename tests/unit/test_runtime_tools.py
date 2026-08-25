import json
from unittest.mock import AsyncMock

import httpx
from tau_web_access import MemorySearchResultStore

from astro import __version__
from astro.backend.models import ProviderStatus
from astro.providers.catalog import collect_model_catalog
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


async def test_model_catalog_combines_tau_models_with_backend_credential_status():
    client = AsyncMock()
    client.list_provider_statuses.return_value = [
        ProviderStatus(provider="openai", status="active", credential_kind="api_key")
    ]

    catalog = await collect_model_catalog(client, session_uid="session-1")
    without_session = await collect_model_catalog(client, session_uid=None)

    openai = next(provider for provider in catalog["providers"] if provider["provider"] == "openai")
    openai_without_session = next(
        provider for provider in without_session["providers"] if provider["provider"] == "openai"
    )
    assert catalog["runtime"] == "tau"
    assert openai["available"] is True
    assert openai["credential_status"] == "active"
    assert openai["models"]
    assert openai_without_session["available"] is False
    assert openai_without_session["credential_status"] == "missing"
    client.list_provider_statuses.assert_awaited_once_with(session_uid="session-1")
