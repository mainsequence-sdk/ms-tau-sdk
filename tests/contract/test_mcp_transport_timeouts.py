"""Per-call response deadlines over real HTTP; mock transports do not enforce read timeouts."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import httpx2
import pytest
from mcp import MCPError, types
from pydantic import ValidationError

from ms_tau_sdk.backend.mcp import MainSequenceMCPClient, MCPConnectionClient
from ms_tau_sdk.settings import TauSDKSettings
from ms_tau_sdk.tools.mainsequence_mcp import create_mainsequence_mcp_tools
from ms_tau_sdk.tools.mcp_connection import TIMEOUT_META_KEY


@dataclass
class _MCPServer:
    url: str = ""
    requests: list[dict] = field(default_factory=list)
    started: dict[str, asyncio.Event] = field(default_factory=dict)
    disconnected: dict[str, asyncio.Event] = field(default_factory=dict)
    handlers: set[asyncio.Task] = field(default_factory=set)
    delays: dict[str, float] = field(default_factory=dict)
    # More tools to list, by name, each with the `_meta` it declares.
    declared_tools: dict[str, dict] = field(default_factory=dict)

    @property
    def calls(self) -> list[dict]:
        return [request for request in self.requests if request.get("method") == "tools/call"]

    async def wait_for_call(self, uid: str) -> None:
        async with asyncio.timeout(2):
            await self.started.setdefault(uid, asyncio.Event()).wait()

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self.handlers.add(task)
        watch: asyncio.Task | None = None
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                (
                    int(line.split(b":", 1)[1])
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                ),
                0,
            )
            request = json.loads(await reader.readexactly(length))
            self.requests.append(request)
            method = request.get("method", "")
            arguments = request.get("params", {}).get("arguments", {})
            delay = arguments.get("delay", self.delays.get(method, 0))
            if method == "tools/call":
                uid = arguments.get("uid", "")
                self.started.setdefault(uid, asyncio.Event()).set()
                closed = self.disconnected.setdefault(uid, asyncio.Event())

                async def observe_close() -> None:
                    if await reader.read(1) == b"":
                        closed.set()

                watch = asyncio.create_task(observe_close())
            if "id" not in request:
                writer.write(
                    b"HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
                return
            if method == "initialize":
                result = {
                    "protocolVersion": request["params"]["protocolVersion"],
                    "capabilities": {"tools": {}, "resources": {}},
                    "serverInfo": {"name": "timeout-test", "version": "1"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": name,
                            "inputSchema": {"type": "object"},
                            "annotations": {"readOnlyHint": readonly, "idempotentHint": readonly},
                        }
                        for name, readonly in [("example.read", True), ("example.write", False)]
                    ]
                    + [
                        {"name": name, "inputSchema": {"type": "object"}, "_meta": meta}
                        for name, meta in self.declared_tools.items()
                    ]
                }
            elif method == "tools/call":
                result = {"content": [{"type": "text", "text": arguments.get("uid", "done")}]}
            elif method == "resources/read":
                result = {"contents": [{"uri": request["params"]["uri"], "text": "resource"}]}
            else:
                result = {"resources": []}
            body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
            # How the answer is delayed; `transport` frees `response_kind` for a2a.send_message.
            response_kind = arguments.get("transport", arguments.get("response_kind", "headers"))
            content_type = "application/json"
            if response_kind == "sse":
                body = b"event: message\ndata: " + body + b"\n\n"
                content_type = "text/event-stream"
            if response_kind == "headers":
                if not await self.wait_for_response(delay, watch):
                    return
            writer.write(
                (
                    f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            if response_kind != "headers":
                if not await self.wait_for_response(delay, watch):
                    return
            writer.write(body)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if watch is not None:
                watch.cancel()
                await asyncio.gather(watch, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            self.handlers.discard(task)

    async def wait_for_response(self, delay: float, watch: asyncio.Task | None) -> bool:
        if watch is None:
            await asyncio.sleep(delay)
            return True
        # The HTTP response timer ends when the client releases its socket; this server does
        # not model remote operation cancellation or perform any real work.
        done, _ = await asyncio.wait({watch}, timeout=delay)
        return watch not in done


@pytest.fixture
async def http_mcp_server() -> AsyncIterator[_MCPServer]:
    """Delay headers/body on an actual socket, without replacing HTTPX's transport."""
    state = _MCPServer()
    server = await asyncio.start_server(state.serve, "127.0.0.1", 0)
    state.url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        yield state
    finally:
        server.close()
        await server.wait_closed()
        for task in tuple(state.handlers):
            task.cancel()
        await asyncio.gather(*tuple(state.handlers), return_exceptions=True)


async def _connect(
    server: _MCPServer,
    kind: str = "platform",
    *,
    default_timeout: float = 0.1,
    http_timeout: float = 0.1,
) -> MCPConnectionClient:
    if kind == "platform":
        auth = AsyncMock()
        auth.headers.return_value = {"Authorization": "Bearer synthetic-test-token"}
        return await MainSequenceMCPClient.connect(
            settings=TauSDKSettings(
                _env_file=None,
                backend_url=server.url,
                backend_read_timeout_seconds=default_timeout,
                turn_timeout_seconds=1,
            ),
            auth=auth,
        )
    return await MCPConnectionClient(
        name="example",
        display_name="Example",
        url=f"{server.url}/mcp",
        auth=None,
        timeout=httpx2.Timeout(connect=1, read=http_timeout, write=1, pool=1),
        read_timeout_seconds=default_timeout,
        read_concurrency=2,
    ).open()


@pytest.mark.parametrize("kind", ["platform", "generic"])
@pytest.mark.parametrize("response_kind", ["headers", "body", "sse"])
async def test_per_call_deadline_allows_responses_beyond_the_http_read_limit(
    http_mcp_server, kind, response_kind
):
    client = await _connect(http_mcp_server, kind)
    try:
        result = await client.call_tool(
            "example.write",
            {"uid": "slow", "delay": 0.3, "response_kind": response_kind},
            timeout_seconds=0.8,
            meta={"example.test/proof": "private-call-metadata"},
        )
        assert result.content[0].text == "slow"
        assert len(http_mcp_server.calls) == 1
        assert http_mcp_server.calls[0]["params"]["_meta"] == {
            "example.test/proof": "private-call-metadata"
        }
        assert client._http_timeout.read == 0.1
    finally:
        await client.aclose()


@pytest.mark.parametrize("response_kind", ["headers", "body", "sse"])
async def test_a_platform_tool_waits_for_the_limit_the_platform_declares_not_the_default(
    http_mcp_server, response_kind
):
    # The session's default response wait, MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS, is 0.1
    # seconds here; the platform declares a longer limit for a2a.send_message.
    http_mcp_server.declared_tools["a2a.send_message"] = {TIMEOUT_META_KEY: 0.8}
    client = await _connect(http_mcp_server)
    try:
        send = next(
            tool
            for tool in create_mainsequence_mcp_tools(client)
            if tool.name == "mainsequence__a2a_send_message"
        )
        # The server answers after 0.3 seconds, past the default and within the declared limit.
        result = await send.execute(
            "call-1",
            {
                "uid": "the target's answer",
                "delay": 0.3,
                "response_kind": "message",
                "transport": response_kind,
            },
        )
        assert result.text == "the target's answer"
        assert len(http_mcp_server.calls) == 1
    finally:
        await client.aclose()


async def test_standalone_platform_calls_can_outlive_the_configured_turn_limit(http_mcp_server):
    client = await _connect(http_mcp_server)
    try:
        result = await client.call_tool(
            "example.read", {"uid": "long", "delay": 1.1}, timeout_seconds=2
        )
        assert result.content[0].text == "long"
        assert len(http_mcp_server.calls) == 1
    finally:
        await client.aclose()


async def test_only_the_tool_response_read_uses_the_per_call_lifetime(http_mcp_server, monkeypatch):
    observed: dict[str, dict] = {}
    real_client = httpx2.AsyncClient

    async def record_timeout(request):
        message = json.loads(request.content)
        observed[message["method"]] = dict(request.extensions["timeout"])

    def observing_client(**kwargs):
        hooks = kwargs.setdefault("event_hooks", {})
        hooks.setdefault("request", []).append(record_timeout)
        return real_client(**kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", observing_client)
    client = await _connect(http_mcp_server)
    try:
        await client.call_tool("example.read", {}, timeout_seconds=0.8)
        await client.read_resource("example://resource")
        for method in ["initialize", "tools/list", "resources/list", "resources/read"]:
            assert observed[method] == {"connect": 10.0, "read": 0.1, "write": 60.0, "pool": 10.0}
        assert observed["tools/call"] == {
            "connect": 10.0,
            "read": None,
            "write": 60.0,
            "pool": 10.0,
        }
    finally:
        await client.aclose()


@pytest.mark.parametrize("override", [None, 0.15])
@pytest.mark.parametrize("response_kind", ["headers", "sse"])
async def test_default_and_explicit_deadlines_expire_without_replay_and_release_http_work(
    http_mcp_server, override, response_kind
):
    client = await _connect(http_mcp_server)
    try:
        async with asyncio.timeout(1):
            started = time.monotonic()
            with pytest.raises(MCPError) as error:
                await client.call_tool(
                    "example.write",
                    {"uid": "expires", "delay": 5, "response_kind": response_kind},
                    timeout_seconds=override,
                )
            # MCP 2.x no longer names the deadline in its message; the wait shows which one ran.
            assert time.monotonic() - started >= (override or 0.1)
            assert error.value.code == types.REQUEST_TIMEOUT
            await http_mcp_server.disconnected["expires"].wait()
            # A response timeout does not poison the connection or replay the operation.
            result = await client.call_tool("example.write", {"uid": "next"})
        assert result.content[0].text == "next"
        assert [call["params"]["arguments"]["uid"] for call in http_mcp_server.calls] == [
            "expires",
            "next",
        ]
        assert client._http_calls == {}
    finally:
        async with asyncio.timeout(1):
            await client.aclose()


async def test_concurrent_read_calls_keep_independent_deadlines(http_mcp_server):
    client = await _connect(http_mcp_server)
    try:
        long = asyncio.create_task(
            client.call_tool("example.read", {"uid": "long", "delay": 0.4}, timeout_seconds=0.8)
        )
        await http_mcp_server.wait_for_call("long")
        with pytest.raises(MCPError):
            await client.call_tool(
                "example.read", {"uid": "short", "delay": 5}, timeout_seconds=0.15
            )
        await http_mcp_server.disconnected["short"].wait()
        assert (await long).content[0].text == "long"
        assert len(http_mcp_server.calls) == 2
        assert client._http_calls == {}
    finally:
        await client.aclose()


async def test_caller_cancellation_releases_http_work_and_does_not_send_queued_writes(
    http_mcp_server,
):
    client = await _connect(http_mcp_server)
    try:
        call = asyncio.create_task(
            client.call_tool("example.write", {"uid": "abandoned", "delay": 5}, timeout_seconds=20)
        )
        await http_mcp_server.wait_for_call("abandoned")
        queued = asyncio.create_task(client.call_tool("example.write", {"uid": "queued"}))
        await asyncio.sleep(0)
        queued.cancel()
        call.cancel()
        async with asyncio.timeout(1):
            for task in [call, queued]:
                with pytest.raises(asyncio.CancelledError):
                    await task
            await http_mcp_server.disconnected["abandoned"].wait()
            result = await client.call_tool("example.write", {"uid": "next"})
        assert result.content[0].text == "next"
        assert [call["params"]["arguments"]["uid"] for call in http_mcp_server.calls] == [
            "abandoned",
            "next",
        ]
        assert client._http_calls == {}
    finally:
        async with asyncio.timeout(1):
            await client.aclose()


@pytest.mark.parametrize("method", ["initialize", "tools/list", "resources/list"])
async def test_startup_keeps_its_short_http_read_limit(http_mcp_server, method):
    http_mcp_server.delays[method] = 0.3
    # Give the session more time than HTTPX, to prove the HTTP startup limit still applies.
    async with asyncio.timeout(1):
        with pytest.raises(httpx2.ReadTimeout):
            await _connect(http_mcp_server, "generic", default_timeout=0.8)
    assert http_mcp_server.calls == []


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), -float("inf"), True])
async def test_invalid_per_call_deadlines_are_rejected_before_the_call_is_queued(seconds):
    client = MainSequenceMCPClient(
        settings=TauSDKSettings(_env_file=None, backend_url="http://unused.test"), auth=AsyncMock()
    )
    with pytest.raises(ValueError, match="finite and greater than zero"):
        await client.call_tool("example.write", {}, timeout_seconds=seconds)
    assert client._commands is None


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), -float("inf")])
def test_the_default_response_deadline_must_be_finite(seconds):
    with pytest.raises(ValidationError):
        TauSDKSettings(_env_file=None, backend_read_timeout_seconds=seconds)
