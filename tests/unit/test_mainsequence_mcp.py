import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, Mock, patch

import anyio
import httpx
import pytest
from mcp import types
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

from ms_tau_sdk.backend.client import LeaseProof
from ms_tau_sdk.backend.mcp import (
    MainSequenceMCPClient,
    _RuntimeCredentialHTTPXAuth,
)
from ms_tau_sdk.runtime.requester import (
    Requester,
    TurnRequesterBinding,
    bind_turn_requester,
    unbind_turn_requester,
)
from ms_tau_sdk.settings import TauSDKSettings
from ms_tau_sdk.tools.mainsequence_mcp import (
    CALLER_SESSION_PROOF_META_KEY,
    DELEGATION_META_KEY,
    create_mainsequence_mcp_tools,
    mainsequence_mcp_resource_prompt,
)


def _settings() -> TauSDKSettings:
    return TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test/",
        runtime_credential_id="credential-id",
    )


@pytest.mark.asyncio
async def test_mcp_http_auth_reuses_runtime_auth_and_refreshes_once():
    runtime_auth = AsyncMock()
    runtime_auth.headers.side_effect = [
        {"Authorization": "Bearer first"},
        {"Authorization": "Bearer second"},
    ]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(401 if len(seen) == 1 else 200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=_RuntimeCredentialHTTPXAuth(runtime_auth),
    ) as client:
        response = await client.post("http://backend.test/mcp", json={})

    assert response.status_code == 200
    assert seen == ["Bearer first", "Bearer second"]
    assert runtime_auth.headers.await_args_list[0].kwargs == {}
    assert runtime_auth.headers.await_args_list[1].kwargs == {"force": True}


def test_mcp_url_is_derived_from_backend_url():
    runtime_auth = AsyncMock()

    client = MainSequenceMCPClient(
        settings=_settings(),
        auth=runtime_auth,
    )

    assert client.url == "http://backend.test/mcp"


@pytest.mark.asyncio
async def test_mcp_client_forwards_private_tool_metadata_only_to_that_call():
    client = MainSequenceMCPClient(
        settings=_settings(),
        auth=AsyncMock(),
    )
    client._commands = asyncio.Queue()
    observed = []

    class FakeSession:
        async def call_tool(self, name, arguments, *, meta=None):
            observed.append((name, arguments, meta))
            return types.CallToolResult(content=[])

    client._owner_task = asyncio.create_task(client._serve(FakeSession()))
    proof = {
        CALLER_SESSION_PROOF_META_KEY: {
            "caller_agent_session_uid": "session-1",
            "lease_holder_id": "holder-1",
            "lease_token": "lease-1",
        }
    }

    await client.call_tool("a2a.send_message", {"message": "hello"}, meta=proof)
    await client.call_tool("agent.list", {})
    await client.aclose()

    assert observed == [
        ("a2a.send_message", {"message": "hello"}, proof),
        ("agent.list", {}, None),
    ]


@pytest.mark.asyncio
async def test_mcp_transport_does_not_leak_cancel_scope_into_streaming_response():
    transport_tasks: list[asyncio.Task[object] | None] = []
    transport_exit_tasks: list[asyncio.Task[object] | None] = []
    operation_tasks: list[asyncio.Task[object] | None] = []

    @asynccontextmanager
    async def fake_transport(*_args, **_kwargs):
        async with anyio.create_task_group() as task_group:
            transport_tasks.append(asyncio.current_task())
            try:
                yield object(), object(), lambda: None
            finally:
                transport_exit_tasks.append(asyncio.current_task())
                task_group.cancel_scope.cancel()

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
            return types.ListToolsResult(tools=[])

        async def list_resources(self):
            return types.ListResourcesResult(resources=[])

        async def call_tool(self, name, arguments, *, meta=None):
            del meta
            operation_tasks.append(asyncio.current_task())
            return types.CallToolResult(content=[])

    clients: list[MainSequenceMCPClient] = []

    async def stream() -> AsyncIterator[bytes]:
        client = await MainSequenceMCPClient.connect(
            settings=_settings(),
            auth=AsyncMock(),
        )
        clients.append(client)
        await client.call_tool("code_repository.list", {})
        yield b"ok"

    async def endpoint(_request):
        return StreamingResponse(stream())

    app = Starlette(routes=[Route("/", endpoint)])
    with (
        patch("ms_tau_sdk.backend.mcp.streamable_http_client", fake_transport),
        patch("ms_tau_sdk.backend.mcp.ClientSession", FakeClientSession),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://ms_tau_sdk.test",
        ) as http:
            response = await http.get("/")

        assert response.text == "ok"
        assert len(clients) == 1
        await clients[0].aclose()

    assert transport_tasks == transport_exit_tasks
    assert operation_tasks == transport_tasks


@pytest.mark.asyncio
async def test_mcp_connect_closes_partial_transport_on_failure():
    with (
        pytest.raises(RuntimeError, match="initialization failed"),
        patch.object(
            MainSequenceMCPClient,
            "_connect",
            AsyncMock(side_effect=RuntimeError("initialization failed")),
        ),
        patch.object(
            MainSequenceMCPClient,
            "aclose",
            AsyncMock(
                side_effect=ExceptionGroup(
                    "cleanup failed",
                    [RuntimeError("secondary transport failure")],
                )
            ),
        ) as close,
    ):
        await MainSequenceMCPClient.connect(
            settings=_settings(),
            auth=AsyncMock(),
        )

    close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_mcp_connect_unwraps_transport_error_from_cancelled_task_group():
    request = httpx.Request("POST", "http://backend.test/mcp")
    response = httpx.Response(421, request=request)
    transport_error = httpx.HTTPStatusError(
        "Invalid Host header",
        request=request,
        response=response,
    )

    with (
        pytest.raises(httpx.HTTPStatusError, match="Invalid Host header"),
        patch.object(
            MainSequenceMCPClient,
            "_connect",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ),
        patch.object(
            MainSequenceMCPClient,
            "aclose",
            AsyncMock(
                side_effect=ExceptionGroup(
                    "transport failed",
                    [transport_error],
                )
            ),
        ),
    ):
        await MainSequenceMCPClient.connect(
            settings=_settings(),
            auth=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_mcp_tools_and_resources_are_exposed_to_tau():
    resource_uri = "mainsequence://platform/skills/code-repository-design"
    client = AsyncMock()
    client.tools = (
        types.Tool(
            name="code_repository.list",
            description="List code repositories.",
            inputSchema={
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "additionalProperties": False,
            },
            annotations=types.ToolAnnotations(title="List code repositories"),
        ),
    )
    client.resources = (
        types.Resource(
            name="code_repository_design",
            uri=resource_uri,
            description="CodeRepository design guidance.",
            mimeType="text/markdown",
        ),
    )
    client.call_tool.return_value = types.CallToolResult(
        content=[types.TextContent(type="text", text="code repository result")],
        structuredContent={"count": 1},
    )
    client.read_resource.return_value = types.ReadResourceResult(
        contents=[
            types.TextResourceContents(
                uri=resource_uri,
                mimeType="text/markdown",
                text="# CodeRepository design",
            )
        ]
    )

    tools = create_mainsequence_mcp_tools(client)

    assert [tool.name for tool in tools] == [
        "mainsequence__code_repository_list",
        "mainsequence__read_resource",
    ]
    code_repository_result = await tools[0].execute("call-1", {"limit": 5})
    assert code_repository_result.text == "code repository result"
    assert code_repository_result.details["structured_content"] == {"count": 1}
    client.call_tool.assert_awaited_once_with("code_repository.list", {"limit": 5})

    resource_result = await tools[1].execute("call-2", {"uri": resource_uri})
    assert resource_result.text == "# CodeRepository design"
    client.read_resource.assert_awaited_once_with(resource_uri)

    prompt = mainsequence_mcp_resource_prompt(client)
    assert "mainsequence__read_resource" in prompt
    assert resource_uri in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text_block", [True, False], ids=["text_and_structured", "structured_only"]
)
async def test_runtime_access_credentials_never_become_agent_tool_results(text_block):
    marker = "SYNTHETIC_RUNTIME_ACCESS_TOKEN_DO_NOT_USE"
    payload = {
        "mode": "token",
        "token": marker,
        "rpc_url": "https://runtime.example.invalid/a2a",
    }
    client = AsyncMock()
    client.tools = (
        types.Tool(
            name="agent_session.resolve_runtime_access",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(name="agent.get", inputSchema={"type": "object", "properties": {}}),
    )
    client.resources = ()
    credential_result = types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload))] if text_block else [],
        structuredContent=payload,
    )
    ordinary_result = types.CallToolResult(
        content=[types.TextContent(type="text", text="agent")],
        structuredContent={"uid": "agent-1"},
    )
    client.call_tool.side_effect = lambda name, arguments: (
        credential_result if name == "agent_session.resolve_runtime_access" else ordinary_result
    )

    tools = create_mainsequence_mcp_tools(client)
    results = [await tool.execute("call-1", {}) for tool in tools]

    assert [tool.name for tool in tools] == ["mainsequence__agent_get"]
    assert results[0].text == "agent"
    assert results[0].details["structured_content"] == {"uid": "agent-1"}
    client.call_tool.assert_awaited_once_with("agent.get", {})
    for result in results:
        assert marker not in json.dumps([block.text for block in result.content])
        assert marker not in json.dumps(result.details)


@pytest.mark.parametrize("tool_name", ["agent.list", "agent.search"])
def test_agent_discovery_tool_hides_backend_controlled_environment_argument(tool_name):
    client = AsyncMock()
    client.tools = (
        types.Tool(
            name=tool_name,
            description="List agents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "organization_environment_uid": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["organization_environment_uid"],
                "additionalProperties": False,
            },
        ),
    )
    client.resources = ()

    tools = create_mainsequence_mcp_tools(client)

    assert "organization_environment_uid" not in tools[0].parameters["properties"]
    assert tools[0].parameters["required"] == []


def test_normalized_mcp_tool_name_collisions_fail_session_setup():
    client = AsyncMock()
    client.tools = (
        types.Tool(name="code_repository.list", inputSchema={"type": "object"}),
        types.Tool(name="code_repository_list", inputSchema={"type": "object"}),
    )
    client.resources = ()

    with pytest.raises(ValueError, match="tool name collision"):
        create_mainsequence_mcp_tools(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"message": "hello"}, "requires response_kind"),
        (
            {"message": "hello", "response_kind": "task"},
            "requires completion_policy",
        ),
        (
            {
                "message": "hello",
                "response_kind": "message",
                "completion_policy": "poll",
            },
            "not valid for Message",
        ),
    ],
)
async def test_a2a_send_requires_explicit_result_and_completion_policy_before_network(
    arguments,
    error,
):
    client = AsyncMock()
    client.tools = (
        types.Tool(
            name="a2a.send_message",
            inputSchema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        ),
    )
    client.resources = ()
    tool = create_mainsequence_mcp_tools(client)[0]

    assert "response_kind" in tool.parameters["required"]
    assert "default" not in tool.parameters["properties"]["response_kind"]
    with pytest.raises(ValueError, match=error):
        await tool.execute("call-1", arguments)

    client.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_a2a_supports_polled_task_without_private_metadata():
    client = AsyncMock()
    client.tools = (
        types.Tool(
            name="a2a.send_message",
            inputSchema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        ),
    )
    client.resources = ()
    client.call_tool.return_value = types.CallToolResult(content=[])
    tool = create_mainsequence_mcp_tools(client)[0]

    assert tool.parameters["properties"]["completion_policy"]["enum"] == ["poll"]
    await tool.execute(
        "call-1",
        {
            "message": "hello",
            "response_kind": "task",
            "completion_policy": "poll",
        },
    )
    with pytest.raises(ValueError, match="requires completion_policy 'poll'"):
        await tool.execute(
            "call-2",
            {
                "message": "hello",
                "response_kind": "task",
                "completion_policy": "resume_caller",
            },
        )

    client.call_tool.assert_awaited_once_with(
        "a2a.send_message",
        {
            "message": "hello",
            "response_kind": "task",
            "completion_policy": "poll",
        },
    )


def test_only_read_only_idempotent_mcp_tools_are_parallel():
    client = AsyncMock()
    client.tools = (
        types.Tool(
            name="code_repository.list",
            inputSchema={"type": "object"},
            annotations=types.ToolAnnotations(
                readOnlyHint=True,
                idempotentHint=True,
            ),
        ),
        types.Tool(
            name="code_repository.create",
            inputSchema={"type": "object"},
            annotations=types.ToolAnnotations(
                readOnlyHint=False,
                idempotentHint=False,
            ),
        ),
        types.Tool(
            name="code_repository.unknown",
            inputSchema={"type": "object"},
        ),
    )
    client.resources = ()

    tools = create_mainsequence_mcp_tools(client)

    assert [tool.execution_mode for tool in tools] == [
        "parallel",
        "sequential",
        "sequential",
    ]


@pytest.mark.asyncio
async def test_process_mcp_runs_safe_reads_concurrently_and_orders_mutations():
    settings = _settings().model_copy(update={"mcp_read_concurrency": 2})
    client = MainSequenceMCPClient(settings=settings, auth=AsyncMock())
    client._commands = asyncio.Queue()
    client._parallel_tool_names = frozenset({"code_repository.get"})
    reads_started = asyncio.Event()
    release_reads = asyncio.Event()
    mutation_started = asyncio.Event()
    active_reads = 0
    maximum_reads = 0

    class FakeSession:
        async def call_tool(self, name, _arguments, *, meta=None):
            del meta
            nonlocal active_reads, maximum_reads
            if name == "code_repository.get":
                active_reads += 1
                maximum_reads = max(maximum_reads, active_reads)
                if active_reads == 2:
                    reads_started.set()
                await release_reads.wait()
                active_reads -= 1
            else:
                mutation_started.set()
            return types.CallToolResult(content=[])

    client._owner_task = asyncio.create_task(client._serve(FakeSession()))
    first = asyncio.create_task(client.call_tool("code_repository.get", {"uid": "one"}))
    second = asyncio.create_task(client.call_tool("code_repository.get", {"uid": "two"}))
    mutation = asyncio.create_task(client.call_tool("code_repository.update", {"uid": "one"}))

    await reads_started.wait()
    await asyncio.sleep(0)
    assert maximum_reads == 2
    assert mutation_started.is_set() is False

    release_reads.set()
    await asyncio.gather(first, second, mutation)
    assert mutation_started.is_set() is True
    await client.aclose()


# --- The private envelope of a hosted call ------------------------------------------------------

SESSION_UID = "11111111-aaaa-4bbb-8ccc-222222222222"
PERSON = Requester(uid="2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d")
LEASE_TOKEN = "SYNTHETIC_LEASE_TOKEN"


def _session_proof(lease_token: str = LEASE_TOKEN) -> dict[str, str]:
    return {
        "caller_agent_session_uid": SESSION_UID,
        "lease_holder_id": "holder-1",
        "lease_token": lease_token,
    }


def _binding(
    *,
    requester: Requester | None,
    session_uid: str = SESSION_UID,
    lease_token: Callable[[], str] = lambda: LEASE_TOKEN,
) -> TurnRequesterBinding:
    return TurnRequesterBinding(
        session_uid=session_uid,
        requester=requester,
        lease_proof=lambda: LeaseProof(
            session_uid=session_uid,
            holder_id="holder-1",
            lease_token=lease_token(),
        ),
        platform=AsyncMock(),
        application_http=Mock(),
    )


@contextmanager
def _turn(binding: TurnRequesterBinding) -> Iterator[None]:
    token = bind_turn_requester(binding)
    try:
        yield
    finally:
        unbind_turn_requester(binding, token)


def _platform(*tools: types.Tool) -> AsyncMock:
    client = AsyncMock()
    client.tools = tools or (types.Tool(name="job.run", inputSchema={"type": "object"}),)
    client.resources = ()
    client.call_tool.return_value = types.CallToolResult(
        content=[types.TextContent(type="text", text="ran")]
    )
    return client


@pytest.mark.asyncio
async def test_a_hosted_call_for_a_person_names_the_session_and_carries_the_delegation():
    client = _platform()
    tool = create_mainsequence_mcp_tools(client, session_uid=SESSION_UID)[0]

    with _turn(_binding(requester=PERSON)):
        result = await tool.execute("call-1", {})

    assert result.text == "ran"
    client.call_tool.assert_awaited_once_with(
        "job.run",
        {},
        meta={
            CALLER_SESSION_PROOF_META_KEY: _session_proof(),
            DELEGATION_META_KEY: _session_proof(),
        },
    )
    assert LEASE_TOKEN not in result.text + json.dumps(result.details)


@pytest.mark.asyncio
async def test_a_hosted_call_that_serves_nobody_names_the_session_and_carries_no_delegation():
    client = _platform()
    tool = create_mainsequence_mcp_tools(client, session_uid=SESSION_UID)[0]

    with _turn(_binding(requester=None)):
        result = await tool.execute("call-1", {})

    assert result.text == "ran"
    client.call_tool.assert_awaited_once_with(
        "job.run", {}, meta={CALLER_SESSION_PROOF_META_KEY: _session_proof()}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["error_result", "raised"])
async def test_a_refused_delegated_call_is_never_retried_as_the_agent(refusal):
    client = _platform()
    if refusal == "error_result":
        client.call_tool.return_value = types.CallToolResult(
            content=[types.TextContent(type="text", text="The delegation was refused.")],
            isError=True,
        )
    else:
        client.call_tool.side_effect = RuntimeError("The delegation was refused.")
    tool = create_mainsequence_mcp_tools(client, session_uid=SESSION_UID)[0]

    with _turn(_binding(requester=PERSON)):
        if refusal == "error_result":
            result = await tool.execute("call-1", {})
            assert result.details["is_error"] is True
        else:
            with pytest.raises(RuntimeError, match="refused"):
                await tool.execute("call-1", {})

    client.call_tool.assert_awaited_once_with(
        "job.run",
        {},
        meta={
            CALLER_SESSION_PROOF_META_KEY: _session_proof(),
            DELEGATION_META_KEY: _session_proof(),
        },
    )


@pytest.mark.asyncio
async def test_tool_metadata_never_decides_the_envelope():
    labelled = types.Tool(
        name="a2a.send_message",
        inputSchema={"type": "object", "properties": {"message": {"type": "string"}}},
        _meta={
            "mainsequence.ai/requires-caller-session-proof/v1": True,
            "mainsequence.ai/requires-requester/v1": True,
        },
    )
    plain = types.Tool(name="agent.get", inputSchema={"type": "object"})
    client = _platform(labelled, plain)
    tools = create_mainsequence_mcp_tools(client, session_uid=SESSION_UID)

    with _turn(_binding(requester=None)):
        await tools[0].execute("call-1", {"message": "hi", "response_kind": "message"})
        await tools[1].execute("call-2", {})

    envelope = {CALLER_SESSION_PROOF_META_KEY: _session_proof()}
    assert [call.kwargs for call in client.call_tool.await_args_list] == [
        {"meta": envelope},
        {"meta": envelope},
    ]


@pytest.mark.asyncio
async def test_the_envelope_is_built_for_each_call_from_the_turn_that_makes_it():
    client = _platform()
    tool = create_mainsequence_mcp_tools(client, session_uid=SESSION_UID)[0]
    tokens = iter(["lease-1", "lease-2", "lease-3"])

    with _turn(_binding(requester=PERSON, lease_token=lambda: next(tokens))):
        await tool.execute("call-1", {})
    with _turn(_binding(requester=None, lease_token=lambda: next(tokens))):
        await tool.execute("call-2", {})

    first, second = (call.kwargs["meta"] for call in client.call_tool.await_args_list)
    assert first == {
        CALLER_SESSION_PROOF_META_KEY: _session_proof("lease-1"),
        DELEGATION_META_KEY: _session_proof("lease-1"),
    }
    assert second == {CALLER_SESSION_PROOF_META_KEY: _session_proof("lease-2")}


@pytest.mark.asyncio
@pytest.mark.parametrize("turn", ["none", "another_session"])
async def test_a_hosted_call_outside_its_sessions_turn_fails_before_it_is_sent(turn):
    client = _platform()
    tool = create_mainsequence_mcp_tools(client, session_uid=SESSION_UID)[0]

    with pytest.raises(RuntimeError, match="inside their session's turn"):
        if turn == "none":
            await tool.execute("call-1", {})
        else:
            with _turn(_binding(requester=PERSON, session_uid="another-session")):
                await tool.execute("call-1", {})

    client.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_mode_calls_carry_no_private_metadata():
    client = _platform()
    tool = create_mainsequence_mcp_tools(client)[0]

    with _turn(_binding(requester=None)):
        result = await tool.execute("call-1", {})

    assert result.text == "ran"
    client.call_tool.assert_awaited_once_with("job.run", {})
