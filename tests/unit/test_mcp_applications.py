import json
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from mcp import types
from pydantic import SecretStr

from ms_tau_sdk.backend.client import LeaseProof
from ms_tau_sdk.backend.models import (
    MCPApplication,
    ReleaseAccessGrant,
    ReleaseRuntimeAccess,
    TauRuntimeBootstrap,
)
from ms_tau_sdk.errors import BackendError, ConfigurationError
from ms_tau_sdk.runtime.requester import (
    Requester,
    RequesterBindingError,
    TurnRequesterBinding,
    _turn_application_access,
    _TurnApplicationAccess,
    bind_turn_requester,
    unbind_turn_requester,
)
from ms_tau_sdk.settings import TauSDKSettings
from ms_tau_sdk.tools.mcp_applications import (
    _ApplicationTokenAuth,
    application_connector,
    create_mcp_application_tools,
)

RELEASE_UID = "6f2c7c52-2b41-4c4e-9c43-36d2e0a8f1aa"
ORDERS = MCPApplication(name="orders", resource_release_uid=RELEASE_UID)
TOKEN = "SYNTHETIC_APPLICATION_TOKEN"


LEASE = LeaseProof(
    session_uid="session-1", holder_id="holder-1", lease_token="SYNTHETIC_LEASE_TOKEN"
)


def _platform() -> AsyncMock:
    platform = AsyncMock()
    platform.resolve_release_runtime_access.return_value = ReleaseRuntimeAccess(
        resource_release_uid=RELEASE_UID,
        access=ReleaseAccessGrant(
            mode="token", token=SecretStr(TOKEN), rpc_url="https://orders.apps.test/"
        ),
    )
    return platform


@contextmanager
def _turn(platform: AsyncMock, requester: Requester | None) -> Iterator[None]:
    binding = TurnRequesterBinding(
        session_uid="session-1",
        requester=requester,
        lease_proof=lambda: LEASE,
        platform=platform,
        application_http=AsyncMock(),
    )
    token = bind_turn_requester(binding)
    try:
        yield
    finally:
        unbind_turn_requester(binding, token)


@pytest.fixture
def person():
    platform = _platform()
    with _turn(platform, Requester(uid="person-1")):
        yield platform


@pytest.fixture
def nobody():
    platform = _platform()
    with _turn(platform, None):
        yield platform


def _client(tools=(), result=None):
    client = AsyncMock()
    client.tools = tuple(tools)
    client.call_tool.return_value = result or types.CallToolResult(
        content=[types.TextContent(type="text", text="three open orders")],
        structuredContent={"count": 3},
    )
    return client


def _tools(connect):
    return {tool.name: tool for tool in create_mcp_application_tools([ORDERS], connect=connect)}


def test_each_declared_application_gets_a_list_and_a_call_tool():
    tools = _tools(AsyncMock())

    assert list(tools) == ["orders__list_tools", "orders__call_tool"]
    assert tools["orders__list_tools"].execution_mode == "parallel"
    assert tools["orders__call_tool"].execution_mode == "sequential"
    assert tools["orders__call_tool"].parameters["required"] == ["tool"]


@pytest.mark.parametrize("name", ["Orders", "mainsequence", "1orders", "orders-api", ""])
def test_an_invalid_or_reserved_application_name_fails_the_session(name):
    with pytest.raises(ConfigurationError):
        create_mcp_application_tools(
            [MCPApplication(name=name, resource_release_uid=RELEASE_UID)], connect=AsyncMock()
        )


def test_a_duplicate_application_name_fails_the_session():
    with pytest.raises(ConfigurationError, match="Duplicate"):
        create_mcp_application_tools([ORDERS, ORDERS], connect=AsyncMock())


@pytest.mark.asyncio
async def test_a_turn_that_serves_nobody_still_calls_the_application(nobody):
    client = _client(tools=[types.Tool(name="orders.list", inputSchema={})])
    connect = AsyncMock(return_value=client)
    tools = _tools(connect)

    listed = await tools["orders__list_tools"].execute("call-1", {})
    called = await tools["orders__call_tool"].execute("call-2", {"tool": "orders.list"})

    for result in (listed, called):
        assert result.details["is_error"] is False
    assert [call.args for call in connect.await_args_list] == [(ORDERS, True), (ORDERS, False)]


@pytest.mark.asyncio
async def test_list_tools_asks_the_application_as_the_person_and_closes_the_session(person):
    client = _client(
        tools=[
            types.Tool(
                name="orders.list",
                description="List open orders.",
                inputSchema={"type": "object", "properties": {"limit": {"type": "integer"}}},
                annotations=types.ToolAnnotations(readOnlyHint=True),
            )
        ]
    )
    connect = AsyncMock(return_value=client)

    result = await _tools(connect)["orders__list_tools"].execute("call-1", {})

    connect.assert_awaited_once_with(ORDERS, True)
    client.aclose.assert_awaited_once_with()
    assert "orders__call_tool" in result.text
    assert json.loads(result.text.split("\n", 1)[1]) == [
        {
            "name": "orders.list",
            "description": "List open orders.",
            "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
            "readOnly": True,
        }
    ]
    assert result.details == {"application": "orders", "is_error": False, "tools": ["orders.list"]}


@pytest.mark.asyncio
async def test_call_tool_calls_one_tool_by_name_without_reading_the_catalog(person):
    client = _client()
    connect = AsyncMock(return_value=client)

    result = await _tools(connect)["orders__call_tool"].execute(
        "call-1", {"tool": "orders.list", "arguments": {"limit": 5}}
    )

    connect.assert_awaited_once_with(ORDERS, False)
    client.call_tool.assert_awaited_once_with("orders.list", {"limit": 5})
    client.aclose.assert_awaited_once_with()
    assert result.text == "three open orders"
    assert result.details["application"] == "orders"
    assert result.details["structured_content"] == {"count": 3}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (RequesterBindingError("ended"), "Your access for this request ended."),
        (
            BackendError("not found", status_code=404),
            "The orders application is not available for this call.",
        ),
        (RuntimeError(f"failed with {TOKEN}"), "The orders application could not be reached."),
    ],
)
async def test_failures_say_what_happened_without_echoing_the_error(person, error, message):
    result = await _tools(AsyncMock(side_effect=error))["orders__call_tool"].execute(
        "call-1", {"tool": "orders.list"}
    )

    assert result.text == message
    assert result.details["is_error"] is True
    assert TOKEN not in result.text + json.dumps(result.details)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments", [{}, {"tool": ""}, {"tool": "orders.list", "arguments": "limit=5"}]
)
async def test_call_tool_needs_a_tool_name_and_object_arguments(person, arguments):
    connect = AsyncMock()

    result = await _tools(connect)["orders__call_tool"].execute("call-1", arguments)

    assert result.details["is_error"] is True
    connect.assert_not_awaited()


def test_the_platform_hands_declared_applications_in_the_startup_data():
    fields = {
        "session": {},
        "lease": {},
        "runtime_state": {},
        "history": {},
        "provider_credentials": {},
        "provider_control": {},
        "runtime_capabilities": {},
    }
    with_applications = TauRuntimeBootstrap.model_construct(
        **fields,
        mcp_applications=[{"name": "orders", "resource_release_uid": RELEASE_UID}],
    )
    without = TauRuntimeBootstrap.model_fields["mcp_applications"]

    parsed = [MCPApplication.model_validate(item) for item in with_applications.mcp_applications]
    assert parsed == [ORDERS]
    assert without.default_factory() == []


@pytest.mark.asyncio
async def test_the_application_token_is_sent_and_renewed_once_on_401():
    renew = AsyncMock(return_value="SECOND_TOKEN")
    auth = _ApplicationTokenAuth(
        _TurnApplicationAccess(
            rpc_url=httpx.URL("https://orders.apps.test/"), token="FIRST_TOKEN", renew=renew
        )
    )
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(401 if len(seen) == 1 else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=auth) as http:
        response = await http.post("https://orders.apps.test/mcp", json={})

    assert response.status_code == 200
    assert seen == ["Bearer FIRST_TOKEN", "Bearer SECOND_TOKEN"]
    renew.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("serving", [True, False], ids=["a_person", "nobody"])
async def test_the_connector_opens_the_applications_mcp_endpoint_with_the_turns_token(serving):
    platform = _platform()
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
            return types.InitializeResult(
                protocolVersion="2025-11-25",
                capabilities=types.ServerCapabilities(tools=types.ToolsCapability()),
                serverInfo=types.Implementation(name="orders", version="1"),
            )

        async def list_tools(self):
            return types.ListToolsResult(tools=[types.Tool(name="orders.list", inputSchema={})])

        async def list_resources(self):
            raise AssertionError("the application offers no resources")

    settings = TauSDKSettings(
        _env_file=None, backend_url="http://backend.test/", runtime_credential_id="credential-id"
    )
    with (
        _turn(platform, Requester(uid="person-1") if serving else None),
        patch("ms_tau_sdk.backend.mcp.streamable_http_client", fake_transport),
        patch("ms_tau_sdk.backend.mcp.ClientSession", FakeClientSession),
    ):
        client = await application_connector(settings)(ORDERS, True)
        await client.aclose()

    assert opened == ["https://orders.apps.test/mcp"]
    assert [tool.name for tool in client.tools] == ["orders.list"]
    assert client.resources == ()
    # The token carries the person's delegation when the turn serves one, and is the Agent's own
    # otherwise. The lease proof goes only to the platform.
    platform.resolve_release_runtime_access.assert_awaited_once_with(
        RELEASE_UID, proof=LEASE if serving else None
    )


@pytest.mark.asyncio
async def test_a_refused_delegation_is_reported_and_never_retried_as_the_agent():
    platform = _platform()
    platform.resolve_release_runtime_access.side_effect = BackendError(
        "refused", status_code=403, detail={"code": "requester_binding_invalid"}
    )
    settings = TauSDKSettings(
        _env_file=None, backend_url="http://backend.test/", runtime_credential_id="credential-id"
    )

    with _turn(platform, Requester(uid="person-1")):
        with pytest.raises(RequesterBindingError):
            await _turn_application_access(RELEASE_UID)
        result = await _tools(application_connector(settings))["orders__call_tool"].execute(
            "call-1", {"tool": "orders.list"}
        )

    assert result.text == "Your access for this request ended."
    assert [call.kwargs for call in platform.resolve_release_runtime_access.await_args_list] == [
        {"proof": LEASE},
        {"proof": LEASE},
    ]


@pytest.mark.asyncio
async def test_each_turn_obtains_its_own_application_access():
    platform = _platform()

    with _turn(platform, Requester(uid="person-1")):
        await _turn_application_access(RELEASE_UID)
        await _turn_application_access(RELEASE_UID)
    with _turn(platform, Requester(uid="person-2")):
        await _turn_application_access(RELEASE_UID)
    with _turn(platform, None):
        await _turn_application_access(RELEASE_UID)

    # A turn reuses its own access, never another turn's or another person's.
    assert [call.kwargs for call in platform.resolve_release_runtime_access.await_args_list] == [
        {"proof": LEASE},
        {"proof": LEASE},
        {"proof": None},
    ]
