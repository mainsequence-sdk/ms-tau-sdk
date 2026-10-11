"""MCP connections over Streamable HTTP; the Main Sequence platform's MCP is the first one."""

from __future__ import annotations

import asyncio
import contextvars
import math
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, Self, cast

import anyio
import httpx
from anyio.abc import ObjectSendStream
from anyio.streams.memory import MemoryObjectSendStream
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
from pydantic import AnyUrl
from structlog.contextvars import bound_contextvars, get_contextvars

from ms_tau_sdk.settings import TauSDKSettings

from .auth import BackendAuth


def _actionable_cleanup_error(error: BaseException) -> Exception | None:
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            actionable = _actionable_cleanup_error(child)
            if actionable is not None:
                return actionable
        return None
    return error if isinstance(error, Exception) else None


class _RuntimeCredentialHTTPXAuth(httpx.Auth):
    requires_request_body = True

    def __init__(self, runtime_auth: BackendAuth) -> None:
        self.runtime_auth = runtime_auth

    async def async_auth_flow(
        self,
        request: httpx.Request,
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers.update(await self.runtime_auth.headers())
        response = yield request
        if response.status_code == 401:
            request.headers.update(await self.runtime_auth.headers(force=True))
            yield request


@dataclass
class _ToolCall:
    cancel_scope: anyio.CancelScope
    request_id: types.RequestId | None = None
    http_cancel_scope: anyio.CancelScope | None = None
    finished: bool = False

    def cancel_if_abandoned(self, result: asyncio.Future[Any]) -> None:
        if result.cancelled():
            self.cancel_scope.cancel()

    def finish(self) -> None:
        self.finished = True
        if self.http_cancel_scope is not None:
            self.http_cancel_scope.cancel()


_tool_call: contextvars.ContextVar[_ToolCall | None] = contextvars.ContextVar(
    "mcp_tool_call", default=None
)


class _ToolCallWriteStream(ObjectSendStream[SessionMessage]):
    """Associate the session's request ID with its call before the transport sends it.

    MCP's HTTP writer runs in another task, so the calling task's context does not reach its
    requests. Keep this association local to the connection, without adding anything to the wire.
    """

    def __init__(
        self,
        stream: MemoryObjectSendStream[SessionMessage],
        calls: dict[types.RequestId, _ToolCall],
    ) -> None:
        self._stream = stream
        self._calls = calls

    async def send(self, item: SessionMessage) -> None:
        message = item.message.root
        call = _tool_call.get()
        if (
            call is not None
            and isinstance(message, types.JSONRPCRequest)
            and message.method == "tools/call"
        ):
            call.request_id = message.id
            self._calls[message.id] = call
        await self._stream.send(item)

    async def aclose(self) -> None:
        await self._stream.aclose()


class _ToolCallHTTPClient:
    """Give each call's HTTP response a cancellation scope inside MCP's transport task.

    Cancelling that task directly cancels MCP's whole task group. A local scope releases the
    response socket when its call ends while leaving other requests and the connection alive.
    """

    def __init__(self, client: httpx.AsyncClient, calls: dict[types.RequestId, _ToolCall]) -> None:
        self._client = client
        self._calls = calls

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    @asynccontextmanager
    async def stream(
        self, method: str, url: httpx.URL | str, **kwargs: Any
    ) -> AsyncIterator[httpx.Response]:
        message = kwargs.get("json", {})
        call = (
            self._calls.get(message.get("id"))
            if method == "POST" and message.get("method") == "tools/call"
            else None
        )
        if call is None:
            async with self._client.stream(method, url, **kwargs) as response:
                yield response
            return

        timeout = httpx.Timeout(self._client.timeout)
        timeout.read = None
        yielded = False
        try:
            with anyio.CancelScope() as http_cancel_scope:
                call.http_cancel_scope = http_cancel_scope
                if call.finished:
                    http_cancel_scope.cancel()
                async with self._client.stream(method, url, timeout=timeout, **kwargs) as response:
                    yielded = True
                    yield response
            if not yielded:
                # Its finite response deadline already ended the call. Let MCP finish this
                # cancelled POST without interpreting it as a failure of the whole connection.
                yield httpx.Response(202)
        finally:
            if call.request_id is not None:
                self._calls.pop(call.request_id, None)


def _validate_response_timeout(seconds: float) -> None:
    if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("MCP response timeout must be finite and greater than zero")


@dataclass(frozen=True)
class _CallToolCommand:
    name: str
    arguments: dict[str, object]
    meta: dict[str, object] | None
    timeout_seconds: float | None
    log_context: dict[str, object]
    result: asyncio.Future[types.CallToolResult]


@dataclass(frozen=True)
class _ReadResourceCommand:
    uri: str
    log_context: dict[str, object]
    result: asyncio.Future[types.ReadResourceResult]


@dataclass(frozen=True)
class _CloseCommand:
    reason: Literal["client_close"] = "client_close"


type _MCPCommand = _CallToolCommand | _ReadResourceCommand | _CloseCommand

ENVIRONMENT_SCOPED_AGENT_TOOLS = frozenset({"agent.list", "agent.search"})
ENVIRONMENT_UID_ARGUMENT = "organization_environment_uid"


async def _no_tools() -> types.ListToolsResult:
    return types.ListToolsResult(tools=[])


async def _no_resources() -> types.ListResourcesResult:
    return types.ListResourcesResult(resources=[])


class MCPConnectionClient:
    """One remote MCP server over Streamable HTTP.

    Each client owns its own MCP session, tool catalog and resources; nothing is shared between
    connections.
    """

    def __init__(
        self,
        *,
        name: str,
        display_name: str,
        url: str,
        auth: httpx.Auth | None,
        timeout: httpx.Timeout,
        read_timeout_seconds: float,
        read_concurrency: int,
    ) -> None:
        _validate_response_timeout(read_timeout_seconds)
        self.name = name
        self.display_name = display_name
        self.url = url
        self._http_auth = auth
        self._http_timeout = timeout
        self._read_timeout_seconds = read_timeout_seconds
        self._read_concurrency = read_concurrency
        self._commands: asyncio.Queue[_MCPCommand] | None = None
        self._owner_task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[None] | None = None
        self._failure: Exception | None = None
        # The results of the calls already handed to the session, so that a failed connection
        # ends them as well as the calls still queued.
        self._in_flight: set[asyncio.Future[Any]] = set()
        self._http_calls: dict[types.RequestId, _ToolCall] = {}
        self.tools: tuple[types.Tool, ...] = ()
        self.resources: tuple[types.Resource, ...] = ()
        self._parallel_tool_names: frozenset[str] = frozenset()
        self._closed = False

    async def open(self) -> Self:
        """Start the session and read the catalog; close everything if that fails."""

        try:
            await self._connect()
        except BaseException as connection_error:
            # Cleanup task groups can raise a secondary ExceptionGroup. Preserve
            # the connection failure because it contains the actionable cause.
            try:
                await self.aclose()
            except BaseException as cleanup_error:
                if isinstance(connection_error, asyncio.CancelledError):
                    actionable = _actionable_cleanup_error(cleanup_error)
                    if actionable is not None:
                        raise actionable from connection_error
            raise
        return self

    async def _connect(self) -> None:
        loop = asyncio.get_running_loop()
        self._commands = asyncio.Queue()
        self._ready = loop.create_future()
        initial_log_context = dict(get_contextvars())
        self._owner_task = asyncio.create_task(
            self._run(initial_log_context),
            name=f"ms-tau-{self.name}-mcp",
            context=contextvars.Context(),
        )
        await asyncio.shield(self._ready)

    async def _run(self, initial_log_context: dict[str, object]) -> None:
        failure: Exception | None = None
        try:
            async with httpx.AsyncClient(
                timeout=self._http_timeout,
                auth=self._http_auth,
            ) as http_client:
                async with streamable_http_client(
                    self.url,
                    http_client=cast(
                        httpx.AsyncClient, _ToolCallHTTPClient(http_client, self._http_calls)
                    ),
                    terminate_on_close=False,
                ) as (read_stream, write_stream, _get_session_id):
                    async with ClientSession(
                        read_stream,
                        # MCP annotates this as a memory stream, but uses only send/aclose.
                        cast(
                            MemoryObjectSendStream[SessionMessage],
                            _ToolCallWriteStream(write_stream, self._http_calls),
                        ),
                        read_timeout_seconds=timedelta(seconds=self._read_timeout_seconds),
                    ) as session:
                        with bound_contextvars(**initial_log_context):
                            initialized = await session.initialize()
                            await self._read_server_catalog(session, initialized)
                            self._parallel_tool_names = frozenset(
                                tool.name
                                for tool in self.tools
                                if tool.annotations is not None
                                and tool.annotations.readOnlyHint is True
                                and tool.annotations.idempotentHint is True
                            )
                        if self._ready is not None and not self._ready.done():
                            self._ready.set_result(None)
                        await self._serve(session)
        except BaseException as error:
            actionable = _actionable_cleanup_error(error)
            failure = actionable or RuntimeError(
                f"{self.display_name} MCP owner task was cancelled"
            )
            self._failure = failure
        finally:
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(
                    failure or RuntimeError(f"{self.display_name} MCP client failed to start")
                )
            self._fail_pending_commands(
                failure or RuntimeError(f"{self.display_name} MCP client closed")
            )
            self._http_calls.clear()

    async def _read_server_catalog(
        self,
        session: ClientSession,
        initialized: types.InitializeResult | None,
    ) -> None:
        # Read only what the server offers; a server without resources answers "not found".
        capabilities = initialized.capabilities if initialized is not None else None
        offers_tools = capabilities is None or capabilities.tools is not None
        offers_resources = capabilities is None or capabilities.resources is not None
        tools_result, resources_result = await asyncio.gather(
            session.list_tools() if offers_tools else _no_tools(),
            session.list_resources() if offers_resources else _no_resources(),
        )
        self.tools = tuple(tools_result.tools)
        self.resources = tuple(resources_result.resources)

    async def _serve(self, session: ClientSession) -> None:
        commands = self._commands
        if commands is None:
            raise RuntimeError(f"{self.display_name} MCP command queue is not initialized")
        active_reads: set[asyncio.Task[None]] = set()
        read_slots = asyncio.Semaphore(self._read_concurrency)

        async def execute(command: _CallToolCommand | _ReadResourceCommand) -> None:
            if command.result.cancelled():
                return
            self._in_flight.add(command.result)
            try:
                with bound_contextvars(**command.log_context):
                    if isinstance(command, _CallToolCommand):
                        with anyio.CancelScope() as cancel_scope:
                            call = _ToolCall(cancel_scope)
                            token = _tool_call.set(call)
                            command.result.add_done_callback(call.cancel_if_abandoned)
                            try:
                                if command.timeout_seconds is None:
                                    tool_result = await session.call_tool(
                                        command.name,
                                        command.arguments,
                                        meta=command.meta,
                                    )
                                else:
                                    tool_result = await session.call_tool(
                                        command.name,
                                        command.arguments,
                                        read_timeout_seconds=timedelta(
                                            seconds=command.timeout_seconds
                                        ),
                                        meta=command.meta,
                                    )
                                if not command.result.done():
                                    command.result.set_result(tool_result)
                            finally:
                                command.result.remove_done_callback(call.cancel_if_abandoned)
                                _tool_call.reset(token)
                                call.finish()
                    else:
                        resource_result = await session.read_resource(AnyUrl(command.uri))
                        if not command.result.done():
                            command.result.set_result(resource_result)
            except Exception as error:
                if not command.result.done():
                    command.result.set_exception(error)
            finally:
                # A transport failure cancels the session's work before its cause is known; the
                # owner task then ends this call with that cause.
                if command.result.done():
                    self._in_flight.discard(command.result)

        async def execute_read(
            command: _CallToolCommand | _ReadResourceCommand,
        ) -> None:
            async with read_slots:
                await execute(command)

        async def drain_reads() -> None:
            if active_reads:
                await asyncio.gather(*tuple(active_reads))

        try:
            while True:
                command = await commands.get()
                if isinstance(command, _CloseCommand):
                    await drain_reads()
                    return
                is_parallel_read = isinstance(command, _ReadResourceCommand) or (
                    isinstance(command, _CallToolCommand)
                    and command.name in self._parallel_tool_names
                )
                if is_parallel_read:
                    task = asyncio.create_task(execute_read(command))
                    active_reads.add(task)
                    task.add_done_callback(active_reads.discard)
                    continue
                await drain_reads()
                await execute(command)
        finally:
            if active_reads:
                for task in active_reads:
                    task.cancel()
                await asyncio.gather(*tuple(active_reads), return_exceptions=True)

    def _fail_pending_commands(self, error: Exception) -> None:
        for result in self._in_flight:
            if not result.done():
                result.set_exception(error)
        self._in_flight.clear()
        if self._commands is None:
            return
        while not self._commands.empty():
            command = self._commands.get_nowait()
            if not isinstance(command, _CloseCommand) and not command.result.done():
                command.result.set_exception(error)

    def _require_commands(self) -> asyncio.Queue[_MCPCommand]:
        if self._closed:
            raise RuntimeError(f"{self.display_name} MCP client is closed")
        if self._owner_task is None or self._commands is None:
            raise RuntimeError(f"{self.display_name} MCP client is not connected")
        if self._owner_task.done():
            raise RuntimeError(f"{self.display_name} MCP owner task stopped") from self._failure
        return self._commands

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
        *,
        meta: dict[str, object] | None = None,
        timeout_seconds: float | None = None,
    ) -> types.CallToolResult:
        """Wait for one tool's answer within its finite, positive per-call response deadline.

        The deadline starts when the queued call reaches the MCP session and replaces the
        session's default response wait. HTTP read inactivity does not shorten it; connect,
        write and pool limits still apply. Timeout or cancellation never replays the call and
        does not establish whether its remote work completed.
        """

        if timeout_seconds is not None:
            _validate_response_timeout(timeout_seconds)
        commands = self._require_commands()
        result: asyncio.Future[types.CallToolResult] = asyncio.get_running_loop().create_future()
        commands.put_nowait(
            _CallToolCommand(
                name=name,
                arguments=arguments,
                meta=meta,
                timeout_seconds=timeout_seconds,
                log_context=dict(get_contextvars()),
                result=result,
            )
        )
        return await result

    async def read_resource(self, uri: str) -> types.ReadResourceResult:
        commands = self._require_commands()
        result: asyncio.Future[types.ReadResourceResult] = (
            asyncio.get_running_loop().create_future()
        )
        commands.put_nowait(
            _ReadResourceCommand(
                uri=uri,
                log_context=dict(get_contextvars()),
                result=result,
            )
        )
        return await result

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._commands is not None and self._owner_task is not None:
            if not self._owner_task.done():
                self._commands.put_nowait(_CloseCommand())
            await self._owner_task
        if self._failure is not None:
            raise self._failure


class MainSequenceMCPClient(MCPConnectionClient):
    """The Main Sequence platform's own MCP, authenticated with the runtime's credential."""

    def __init__(
        self,
        *,
        settings: TauSDKSettings,
        auth: BackendAuth,
    ) -> None:
        super().__init__(
            name="mainsequence",
            display_name="Main Sequence",
            url=f"{settings.backend_url.rstrip('/')}/mcp",
            auth=_RuntimeCredentialHTTPXAuth(auth),
            timeout=httpx.Timeout(
                connect=settings.backend_connect_timeout_seconds,
                read=settings.backend_read_timeout_seconds,
                write=settings.backend_write_timeout_seconds,
                pool=settings.backend_pool_timeout_seconds,
            ),
            read_timeout_seconds=settings.backend_read_timeout_seconds,
            read_concurrency=settings.mcp_read_concurrency,
        )
        self._settings = settings
        self._auth = auth

    @classmethod
    async def connect(
        cls,
        *,
        settings: TauSDKSettings,
        auth: BackendAuth,
    ) -> MainSequenceMCPClient:
        return await cls(settings=settings, auth=auth).open()
