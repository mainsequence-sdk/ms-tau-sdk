import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from mcp import McpError, types
from mcp.server.fastmcp import FastMCP
from pydantic import SecretStr
from structlog.testing import capture_logs

from ms_tau_sdk.backend.client import LeaseProof, MainSequenceClient
from ms_tau_sdk.backend.models import (
    MCPApplication,
    ReleaseAccessGrant,
    ReleaseRuntimeAccess,
    TauRuntimeBootstrap,
)
from ms_tau_sdk.errors import BackendError, BackendTimeoutError, ConfigurationError
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
    TIMEOUT_META_KEY,
    _ApplicationTokenAuth,
    application_connector,
    create_mcp_application_tools,
)

RELEASE_UID = "6f2c7c52-2b41-4c4e-9c43-36d2e0a8f1aa"
ORDERS = MCPApplication(name="orders", resource_release_uid=RELEASE_UID)
TOKEN = "SYNTHETIC_APPLICATION_TOKEN"
TOOL_TIMEOUT = 60.0
OPEN_TIMEOUT = 30.0


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


def _create(applications, connect):
    return create_mcp_application_tools(
        applications,
        connect=connect,
        tool_timeout_seconds=TOOL_TIMEOUT,
        open_timeout_seconds=OPEN_TIMEOUT,
    )


def _tools(connect):
    return {tool.name: tool for tool in _create([ORDERS], connect)}


def test_each_declared_application_gets_a_list_and_a_call_tool():
    tools = _tools(AsyncMock())

    assert list(tools) == ["orders__list_tools", "orders__call_tool"]
    assert tools["orders__list_tools"].execution_mode == "parallel"
    assert tools["orders__call_tool"].execution_mode == "sequential"
    assert tools["orders__call_tool"].parameters["required"] == ["tool"]


@pytest.mark.parametrize("name", ["Orders", "mainsequence", "1orders", "orders-api", ""])
def test_an_invalid_or_reserved_application_name_fails_the_session(name):
    with pytest.raises(ConfigurationError):
        _create([MCPApplication(name=name, resource_release_uid=RELEASE_UID)], AsyncMock())


def test_a_duplicate_application_name_fails_the_session():
    with pytest.raises(ConfigurationError, match="Duplicate"):
        _create([ORDERS, ORDERS], AsyncMock())


@pytest.mark.asyncio
async def test_a_turn_that_serves_nobody_still_calls_the_application(nobody):
    client = _client(tools=[types.Tool(name="orders.list", inputSchema={})])
    connect = AsyncMock(return_value=client)
    tools = _tools(connect)

    listed = await tools["orders__list_tools"].execute("call-1", {})
    called = await tools["orders__call_tool"].execute("call-2", {"tool": "orders.list"})

    for result in (listed, called):
        assert result.details["is_error"] is False
    assert [call.args for call in connect.await_args_list] == [(ORDERS,), (ORDERS,)]


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

    connect.assert_awaited_once_with(ORDERS)
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


def _tool(name="orders.list", **meta):
    return types.Tool(name=name, inputSchema={}, _meta=meta or None)


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://orders.apps.test/mcp")
    return httpx.HTTPStatusError(
        f"failed with {TOKEN}", request=request, response=httpx.Response(status, request=request)
    )


def _timeout_error() -> McpError:
    return McpError(types.ErrorData(code=httpx.codes.REQUEST_TIMEOUT, message=f"waited {TOKEN}"))


@pytest.mark.asyncio
async def test_call_tool_reads_the_tool_list_and_calls_one_tool_by_name(person):
    client = _client(tools=[_tool()])
    connect = AsyncMock(return_value=client)

    result = await _tools(connect)["orders__call_tool"].execute(
        "call-1", {"tool": "orders.list", "arguments": {"limit": 5}}
    )

    connect.assert_awaited_once_with(ORDERS)
    client.call_tool.assert_awaited_once_with(
        "orders.list", {"limit": 5}, timeout_seconds=TOOL_TIMEOUT
    )
    client.aclose.assert_awaited_once_with()
    assert result.text == "three open orders"
    assert result.details["application"] == "orders"
    assert result.details["structured_content"] == {"count": 3}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tools", "seconds"),
    [
        ([_tool(**{TIMEOUT_META_KEY: 300})], 300.0),
        ([_tool(**{TIMEOUT_META_KEY: 2.5})], 2.5),
        ([_tool()], TOOL_TIMEOUT),
        ([_tool(other="value")], TOOL_TIMEOUT),
        ([_tool("orders.other", **{TIMEOUT_META_KEY: 300})], TOOL_TIMEOUT),
        ([], TOOL_TIMEOUT),
    ],
    ids=["declared", "fractional", "none", "other_meta", "other_tool", "unlisted"],
)
async def test_a_call_waits_for_the_tools_declared_time_or_the_default(person, tools, seconds):
    client = _client(tools=tools)

    await _tools(AsyncMock(return_value=client))["orders__call_tool"].execute(
        "call-1", {"tool": "orders.list"}
    )

    client.call_tool.assert_awaited_once_with("orders.list", {}, timeout_seconds=seconds)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, -5, "300", True, float("nan"), float("inf"), None, [300]])
async def test_an_invalid_declared_time_means_the_default_and_is_logged(person, value):
    client = _client(tools=[_tool(**{TIMEOUT_META_KEY: value})])

    with capture_logs() as logs:
        await _tools(AsyncMock(return_value=client))["orders__call_tool"].execute(
            "call-1", {"tool": "orders.list"}
        )

    client.call_tool.assert_awaited_once_with("orders.list", {}, timeout_seconds=TOOL_TIMEOUT)
    invalid = [log for log in logs if log["event"] == "runtime.mcp_application.invalid_timeout"]
    assert invalid == (
        []
        if value is None
        else [
            {
                "event": "runtime.mcp_application.invalid_timeout",
                "log_level": "warning",
                "application": "orders",
                "mcp_tool": "orders.list",
            }
        ]
    )


OPEN_FAILURES = [
    (
        RequesterBindingError("ended"),
        "Your access for this request ended.",
        {"failure": "access_ended"},
    ),
    (
        BackendError("not found", status_code=404),
        "The orders application is not available for this call.",
        {"failure": "not_available"},
    ),
    (
        BackendError(f"failed with {TOKEN}", status_code=500),
        "Main Sequence could not provide access to the orders application.",
        {"failure": "access_unavailable"},
    ),
    (
        _timeout_error(),
        "The orders application did not answer within 30 seconds.",
        {"failure": "timeout", "timeout_seconds": OPEN_TIMEOUT},
    ),
    (
        httpx.ConnectError(f"failed with {TOKEN}"),
        "The orders application could not be reached.",
        {"failure": "unreachable"},
    ),
    (
        httpx.ConnectTimeout(f"failed with {TOKEN}"),
        "The orders application could not be reached.",
        {"failure": "unreachable"},
    ),
    (
        _status_error(502),
        "The orders application answered with a server error (502).",
        {"failure": "server_error", "status": 502},
    ),
    (
        _status_error(401),
        "The orders application refused this call (401).",
        {"failure": "refused", "status": 401},
    ),
    (
        RuntimeError(f"failed with {TOKEN}"),
        "The orders application could not complete this call.",
        {"failure": "failed"},
    ),
]

CALL_FAILURES = [
    (
        TimeoutError(f"failed with {TOKEN}"),
        "The orders tool orders.list did not answer within 60 seconds.",
        {"failure": "timeout", "timeout_seconds": TOOL_TIMEOUT},
    ),
    *[
        (
            _status_error(status),
            f"The orders application or its gateway reported a timeout ({status}).",
            {"failure": "timeout", "status": status},
        )
        for status in (408, 504)
    ],
    (
        _timeout_error(),
        "The orders tool orders.list did not answer within 60 seconds.",
        {"failure": "timeout", "timeout_seconds": TOOL_TIMEOUT},
    ),
    (
        httpx.ReadTimeout(f"failed with {TOKEN}"),
        "The orders tool orders.list did not answer within 60 seconds.",
        {"failure": "timeout", "timeout_seconds": TOOL_TIMEOUT},
    ),
    (
        _status_error(502),
        "The orders application answered with a server error (502).",
        {"failure": "server_error", "status": 502},
    ),
    (
        _status_error(503),
        "The orders application answered with a server error (503).",
        {"failure": "server_error", "status": 503},
    ),
    (
        _status_error(403),
        "The orders application refused this call (403).",
        {"failure": "refused", "status": 403},
    ),
    (
        _status_error(422),
        "The orders application answered with an error (422).",
        {"failure": "error_status", "status": 422},
    ),
    (
        McpError(types.ErrorData(code=-32603, message=f"failed with {TOKEN}")),
        "The orders application could not complete this call.",
        {"failure": "failed"},
    ),
    (
        httpx.RemoteProtocolError(f"failed with {TOKEN}"),
        "The orders application could not complete this call.",
        {"failure": "failed"},
    ),
]


def _assert_failure(result, message, failure, logs, operation):
    assert result.text == message
    assert result.details == {"application": "orders", "is_error": True, **failure}
    assert TOKEN not in result.text + json.dumps(result.details) + json.dumps(logs, default=str)
    assert [log for log in logs if log["event"] == "runtime.mcp_application.failed"] == [
        {
            "event": "runtime.mcp_application.failed",
            "log_level": "warning",
            "application": "orders",
            "operation": operation,
            "error_type": logs[-1]["error_type"],
            **{key: value for key, value in failure.items()},
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "message", "failure"), OPEN_FAILURES)
async def test_a_failure_while_the_session_opens_says_what_happened(
    person, error, message, failure
):
    with capture_logs() as logs:
        listed = await _tools(AsyncMock(side_effect=[error, error]))["orders__list_tools"].execute(
            "call-1", {}
        )
    _assert_failure(listed, message, failure, logs, "list_tools")

    with capture_logs() as logs:
        called = await _tools(AsyncMock(side_effect=[error, error]))["orders__call_tool"].execute(
            "call-2", {"tool": "orders.list"}
        )
    _assert_failure(called, message, {**failure, "mcp_tool": "orders.list"}, logs, "call_tool")


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "message", "failure"), CALL_FAILURES)
async def test_a_failure_after_the_call_is_sent_says_what_happened(person, error, message, failure):
    client = _client(tools=[_tool()])
    client.call_tool.side_effect = error
    connect = AsyncMock(return_value=client)

    with capture_logs() as logs:
        result = await _tools(connect)["orders__call_tool"].execute(
            "call-1", {"tool": "orders.list"}
        )

    _assert_failure(result, message, {**failure, "mcp_tool": "orders.list"}, logs, "call_tool")
    # A call that was sent is never sent again.
    connect.assert_awaited_once_with(ORDERS)
    client.call_tool.assert_awaited_once()
    client.aclose.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("slow"),
        httpx.RemoteProtocolError("dropped"),
        _timeout_error(),
        _status_error(408),
        _status_error(502),
        _status_error(503),
    ],
    ids=["connect", "connect_timeout", "dropped", "timeout", "408", "502", "503"],
)
@pytest.mark.parametrize("operation", ["orders__list_tools", "orders__call_tool"])
async def test_opening_the_session_is_tried_once_more_and_the_second_answer_counts(
    person, error, operation
):
    client = _client(tools=[_tool()])
    connect = AsyncMock(side_effect=[error, client])

    result = await _tools(connect)[operation].execute("call-1", {"tool": "orders.list"})

    assert result.details["is_error"] is False
    assert connect.await_count == 2


@pytest.mark.asyncio
async def test_a_second_failure_while_opening_is_reported_and_not_retried_again(person):
    connect = AsyncMock(side_effect=[_status_error(502), _status_error(503), _client()])

    result = await _tools(connect)["orders__call_tool"].execute("call-1", {"tool": "orders.list"})

    assert result.text == "The orders application answered with a server error (503)."
    assert connect.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        RequesterBindingError("ended"),
        BackendError("refused", status_code=403),
        BackendError("unavailable", status_code=503),
        _status_error(401),
        _status_error(403),
        _status_error(404),
        RuntimeError("bug"),
    ],
    ids=["access_ended", "platform_403", "platform_503", "401", "403", "404", "other"],
)
async def test_a_refusal_or_other_failure_while_opening_is_not_retried(person, error):
    connect = AsyncMock(side_effect=[error, _client()])

    result = await _tools(connect)["orders__call_tool"].execute("call-1", {"tool": "orders.list"})

    assert result.details["is_error"] is True
    connect.assert_awaited_once_with(ORDERS)


@pytest.mark.asyncio
async def test_a_cancelled_call_is_not_retried(person):
    signal = Mock()
    signal.is_cancelled.side_effect = [False, True]
    connect = AsyncMock(side_effect=[_status_error(502), _client()])

    result = await _tools(connect)["orders__call_tool"].execute(
        "call-1", {"tool": "orders.list"}, signal
    )

    assert result.text == "The orders application answered with a server error (502)."
    connect.assert_awaited_once_with(ORDERS)


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
        client = await application_connector(settings)(ORDERS)
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


# The real MCP client library, talking HTTP to an application that answers in plain JSON.


@pytest.mark.asyncio
@pytest.mark.parametrize("renew", [False, True], ids=["initial_access", "renewal"])
@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout, 408, 504],
)
async def test_platform_timeouts_reach_the_tool_result_without_becoming_access_unavailable(
    mcp_server, renew, failure
):
    settings = TauSDKSettings(_env_file=None, backend_url="https://platform.test/")
    requests = []
    access = _platform().resolve_release_runtime_access.return_value

    def platform_answer(request):
        requests.append(request)
        if renew and len(requests) == 1:
            return httpx.Response(
                200,
                json=access.model_dump(mode="json")
                | {
                    "access": {
                        "mode": "token",
                        "token": TOKEN,
                        "rpc_url": "https://orders.apps.test/",
                    }
                },
            )
        if isinstance(failure, int):
            return httpx.Response(failure, text=f"private response {TOKEN}")
        raise failure(f"private error {TOKEN}", request=request)

    # Construct the platform client before the fixture installs the MCP HTTP transport.
    async with httpx.AsyncClient(
        base_url=settings.backend_url,
        timeout=OPEN_TIMEOUT,
        transport=httpx.MockTransport(platform_answer),
    ) as http:
        auth = Mock()
        auth.headers = AsyncMock(return_value={"Authorization": f"Bearer {TOKEN}"})
        platform = MainSequenceClient(settings, auth, client=http)

        async def refused(_message):
            return httpx.Response(401)

        received = mcp_server(tools=[ORDERS_LIST], on_call=refused)
        with _turn(platform, Requester(uid="person-1")), capture_logs() as logs:
            result = await _finishes(
                _connected_tools()["orders__call_tool"].execute("call-1", {"tool": "orders.list"})
            )

    assert (
        result.text == "Main Sequence timed out while providing access to the orders application."
    )
    assert result.details == {
        "application": "orders",
        "mcp_tool": "orders.list",
        "is_error": True,
        "failure": "timeout",
        "phase": "access",
        **({"status": failure} if isinstance(failure, int) else {"timeout_seconds": OPEN_TIMEOUT}),
    }
    assert len(requests) == (2 if renew else 1)
    assert received.count("tools/call") == (1 if renew else 0)
    assert TOKEN not in result.text + json.dumps(result.details) + json.dumps(logs, default=str)


@pytest.mark.asyncio
async def test_sanitizing_an_access_timeout_preserves_its_limit_without_its_request(person):
    error = BackendTimeoutError("Backend request timed out", timeout_seconds=12)
    error.__cause__ = httpx.ReadTimeout(
        TOKEN,
        request=httpx.Request("POST", "https://platform.test/", headers={"Authorization": TOKEN}),
    )
    person.resolve_release_runtime_access.side_effect = error

    with pytest.raises(BackendTimeoutError) as raised:
        await _turn_application_access(RELEASE_UID)

    assert raised.value.timeout_seconds == 12
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert TOKEN not in str(raised.value)


def _connected_tools(**settings):
    resolved = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test/",
        runtime_credential_id="credential-id",
        **settings,
    )
    tools = create_mcp_application_tools(
        [ORDERS],
        connect=application_connector(resolved),
        tool_timeout_seconds=resolved.mcp_tool_timeout_seconds,
        open_timeout_seconds=resolved.backend_read_timeout_seconds,
    )
    return {tool.name: tool for tool in tools}


def _answer(message, text="done") -> httpx.Response:
    result = {"content": [{"type": "text", "text": text}], "isError": False}
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})


def _slow(seconds: float) -> Callable[[dict], Awaitable[httpx.Response]]:
    async def answer(message):
        await asyncio.sleep(seconds)
        return _answer(message, f"done after {seconds}s")

    return answer


ORDERS_LIST = {"name": "orders.list", "inputSchema": {"type": "object"}}


async def _finishes[T](call: Awaitable[T], seconds: float = 2) -> T:
    """Await ``call``, failing the test if it is still waiting after ``seconds``."""

    task = asyncio.ensure_future(call)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if task not in done:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        pytest.fail(f"the call was still waiting after {seconds} seconds")
    return task.result()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "message"),
    [
        (403, "The orders application refused this call (403)."),
        (502, "The orders application answered with a server error (502)."),
        (503, "The orders application answered with a server error (503)."),
    ],
)
async def test_an_error_status_on_the_call_ends_it_at_once_and_is_not_retried(
    person, mcp_server, status, message
):
    async def refuse(_message):
        return httpx.Response(status, text="gateway error")

    received = mcp_server(tools=[ORDERS_LIST], on_call=refuse)
    tools = _connected_tools(mcp_tool_timeout_seconds=5)

    result = await _finishes(tools["orders__call_tool"].execute("call-1", {"tool": "orders.list"}))

    assert result.text == message
    assert received.count("tools/call") == 1


@pytest.mark.asyncio
async def test_a_tool_runs_past_the_backend_read_timeout_within_its_declared_time(
    person, mcp_server
):
    # The application declares the limit in the MCP Python SDK's tool decorator.
    application = FastMCP("orders")

    @application.tool(name="orders.list", meta={TIMEOUT_META_KEY: 2}, structured_output=False)
    async def orders_list() -> str:
        return "unused"

    listed = [
        tool.model_dump(by_alias=True, exclude_none=True, mode="json")
        for tool in await application.list_tools()
    ]
    mcp_server(tools=listed, on_call=_slow(0.5))
    tools = _connected_tools(backend_read_timeout_seconds=0.2, mcp_tool_timeout_seconds=0.2)

    result = await tools["orders__call_tool"].execute("call-1", {"tool": "orders.list"})

    assert result.text == "done after 0.5s"
    assert result.details["is_error"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "message"),
    [
        (
            {**ORDERS_LIST, "_meta": {TIMEOUT_META_KEY: 0.2}},
            "The orders tool orders.list did not answer within 0.2 seconds.",
        ),
        (ORDERS_LIST, "The orders tool orders.list did not answer within 0.3 seconds."),
    ],
    ids=["declared", "default"],
)
async def test_a_tool_past_its_time_says_it_did_not_answer_and_after_how_long(
    person, mcp_server, tool, message
):
    mcp_server(tools=[tool], on_call=_slow(5))
    tools = _connected_tools(mcp_tool_timeout_seconds=0.3)
    started = time.monotonic()

    result = await tools["orders__call_tool"].execute("call-1", {"tool": "orders.list"})

    assert result.text == message
    assert time.monotonic() - started < 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["initialize", "tools/list"])
async def test_a_gateway_502_while_the_session_opens_is_retried_once(person, mcp_server, method):
    async def answer(message):
        return _answer(message)

    def first_fails(received_method, received):
        if received_method == method and received.count(method) == 1:
            return httpx.Response(502, text="bad gateway")
        return None

    received = mcp_server(tools=[ORDERS_LIST], on_call=answer, on_open=first_fails)

    result = await _connected_tools()["orders__call_tool"].execute(
        "call-1", {"tool": "orders.list"}
    )

    assert result.text == "done"
    assert received.count("initialize") == 2
    assert received.count("tools/call") == 1
