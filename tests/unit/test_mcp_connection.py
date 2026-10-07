import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from mcp import types

from ms_tau_sdk.backend.mcp import MainSequenceMCPClient, MCPConnectionClient
from ms_tau_sdk.settings import TauSDKSettings
from ms_tau_sdk.tools.mainsequence_mcp import create_mainsequence_mcp_tools
from ms_tau_sdk.tools.mcp_connection import MCPToolPolicy, create_mcp_connection_tools


def _connection(tool_names: tuple[str, ...], resource_uri: str | None = None) -> AsyncMock:
    client = AsyncMock()
    client.tools = tuple(
        types.Tool(
            name=name,
            inputSchema={
                "type": "object",
                "properties": {"organization_environment_uid": {"type": "string"}},
                "required": ["organization_environment_uid"],
            },
        )
        for name in tool_names
    )
    client.resources = (
        (types.Resource(name="guide", uri=resource_uri, mimeType="text/markdown"),)
        if resource_uri
        else ()
    )
    client.call_tool.return_value = types.CallToolResult(
        content=[types.TextContent(type="text", text="answer")]
    )
    return client


@pytest.mark.asyncio
async def test_a_second_connection_uses_its_own_prefix_filter_and_client():
    platform = _connection(("agent.list",))
    application = _connection(("orders.list", "orders.delete"), "app://guide")

    platform_tools = create_mainsequence_mcp_tools(platform)
    application_tools = create_mcp_connection_tools(
        application,
        MCPToolPolicy(
            prefix="orders",
            display_name="Orders",
            excluded_tools=frozenset({"orders.delete"}),
        ),
    )

    assert [tool.name for tool in platform_tools] == ["mainsequence__agent_list"]
    assert [tool.name for tool in application_tools] == [
        "orders__orders_list",
        "orders__read_resource",
    ]
    # Only the platform's own policy hides the Environment argument.
    assert "organization_environment_uid" not in platform_tools[0].parameters["properties"]
    assert application_tools[0].parameters == application.tools[0].inputSchema
    assert application_tools[1].description == "Read one advertised Orders resource from MCP."

    await application_tools[0].execute("call-1", {"organization_environment_uid": "env"})
    application.call_tool.assert_awaited_once_with(
        "orders.list", {"organization_environment_uid": "env"}
    )
    platform.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_connection_sends_no_private_metadata_unless_its_policy_adds_it():
    application = _connection(("orders.list",))
    tool = create_mcp_connection_tools(
        application, MCPToolPolicy(prefix="orders", display_name="Orders")
    )[0]

    await tool.execute("call-1", {})

    application.call_tool.assert_awaited_once_with("orders.list", {})


@pytest.mark.asyncio
async def test_a_policy_refusal_is_returned_before_the_call_is_sent():
    application = _connection(("orders.list",))
    tool = create_mcp_connection_tools(
        application,
        MCPToolPolicy(prefix="orders", display_name="Orders", refusal=lambda _tool: "Not now."),
    )[0]

    result = await tool.execute("call-1", {})

    assert result.text == "Not now."
    assert result.details == {"mcp_tool": "orders.list", "is_error": True, "refused": True}
    application.call_tool.assert_not_awaited()


def test_tool_names_collide_only_within_one_connection():
    with pytest.raises(ValueError, match="Orders MCP tool name collision"):
        create_mcp_connection_tools(
            _connection(("orders.list", "orders_list")),
            MCPToolPolicy(prefix="orders", display_name="Orders"),
        )


@pytest.mark.asyncio
async def test_each_connection_client_opens_its_own_session_and_catalog():
    opened: list[str] = []

    @asynccontextmanager
    async def fake_transport(url, **_kwargs):
        opened.append(url)
        yield object(), object(), lambda: None

    class FakeClientSession:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            return None

        async def list_tools(self):
            name = "orders.list" if opened[-1].endswith("/orders/mcp") else "agent.list"
            return types.ListToolsResult(tools=[types.Tool(name=name, inputSchema={})])

        async def list_resources(self):
            return types.ListResourcesResult(resources=[])

    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test/",
        runtime_credential_id="credential-id",
    )
    with (
        patch("ms_tau_sdk.backend.mcp.streamable_http_client", fake_transport),
        patch("ms_tau_sdk.backend.mcp.ClientSession", FakeClientSession),
    ):
        platform = await MainSequenceMCPClient.connect(settings=settings, auth=AsyncMock())
        application = await MCPConnectionClient(
            name="orders",
            display_name="Orders",
            url="http://apps.test/orders/mcp",
            auth=None,
            timeout=httpx.Timeout(5.0),
            read_timeout_seconds=5.0,
            read_concurrency=2,
        ).open()
        tasks = {task.get_name() for task in asyncio.all_tasks()}
        await platform.aclose()
        await application.aclose()

    assert opened == ["http://backend.test/mcp", "http://apps.test/orders/mcp"]
    assert [tool.name for tool in platform.tools] == ["agent.list"]
    assert [tool.name for tool in application.tools] == ["orders.list"]
    assert {"ms-tau-mainsequence-mcp", "ms-tau-orders-mcp"} <= tasks
