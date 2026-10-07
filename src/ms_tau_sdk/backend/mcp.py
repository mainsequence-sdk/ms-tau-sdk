"""MCP connections over Streamable HTTP; the Main Sequence platform's MCP is the first one."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Self

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
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


@dataclass(frozen=True)
class _CallToolCommand:
    name: str
    arguments: dict[str, object]
    meta: dict[str, object] | None
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
        read_catalog: bool = True,
    ) -> None:
        self.name = name
        self._read_catalog = read_catalog
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
                    http_client=http_client,
                    terminate_on_close=False,
                ) as (read_stream, write_stream, _get_session_id):
                    async with ClientSession(
                        read_stream,
                        write_stream,
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

    async def _read_server_catalog(
        self,
        session: ClientSession,
        initialized: types.InitializeResult | None,
    ) -> None:
        if not self._read_catalog:
            return
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
            try:
                with bound_contextvars(**command.log_context):
                    if isinstance(command, _CallToolCommand):
                        tool_result = await session.call_tool(
                            command.name,
                            command.arguments,
                            meta=command.meta,
                        )
                        if not command.result.done():
                            command.result.set_result(tool_result)
                    else:
                        resource_result = await session.read_resource(AnyUrl(command.uri))
                        if not command.result.done():
                            command.result.set_result(resource_result)
            except Exception as error:
                if not command.result.done():
                    command.result.set_exception(error)

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
    ) -> types.CallToolResult:
        commands = self._require_commands()
        result: asyncio.Future[types.CallToolResult] = asyncio.get_running_loop().create_future()
        commands.put_nowait(
            _CallToolCommand(
                name=name,
                arguments=arguments,
                meta=meta,
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
