"""Main Sequence MCP transport using Astro's existing backend authentication."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl
from structlog.contextvars import bound_contextvars, get_contextvars

from astro.settings import Settings

from .auth import RuntimeCredentialAuth


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

    def __init__(self, runtime_auth: RuntimeCredentialAuth) -> None:
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
ENVIRONMENT_UID_ARGUMENT = "organization_project_environment_uid"


class MainSequenceMCPClient:
    def __init__(
        self,
        *,
        settings: Settings,
        auth: RuntimeCredentialAuth,
    ) -> None:
        self.url = f"{settings.backend_url.rstrip('/')}/mcp"
        self._settings = settings
        self._auth = auth
        self._commands: asyncio.Queue[_MCPCommand] | None = None
        self._owner_task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[None] | None = None
        self._failure: Exception | None = None
        self.tools: tuple[types.Tool, ...] = ()
        self.resources: tuple[types.Resource, ...] = ()
        self._closed = False

    @classmethod
    async def connect(
        cls,
        *,
        settings: Settings,
        auth: RuntimeCredentialAuth,
    ) -> MainSequenceMCPClient:
        client = cls(settings=settings, auth=auth)
        try:
            await client._connect()
        except BaseException as connection_error:
            # Cleanup task groups can raise a secondary ExceptionGroup. Preserve
            # the connection failure because it contains the actionable cause.
            try:
                await client.aclose()
            except BaseException as cleanup_error:
                if isinstance(connection_error, asyncio.CancelledError):
                    actionable = _actionable_cleanup_error(cleanup_error)
                    if actionable is not None:
                        raise actionable from connection_error
            raise
        return client

    async def _connect(self) -> None:
        loop = asyncio.get_running_loop()
        self._commands = asyncio.Queue()
        self._ready = loop.create_future()
        initial_log_context = dict(get_contextvars())
        self._owner_task = asyncio.create_task(
            self._run(initial_log_context),
            name="astro-mainsequence-mcp",
            context=contextvars.Context(),
        )
        await asyncio.shield(self._ready)

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self._settings.backend_connect_timeout_seconds,
            read=self._settings.backend_read_timeout_seconds,
            write=self._settings.backend_write_timeout_seconds,
            pool=self._settings.backend_pool_timeout_seconds,
        )

    async def _run(self, initial_log_context: dict[str, object]) -> None:
        failure: Exception | None = None
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout(),
                auth=_RuntimeCredentialHTTPXAuth(self._auth),
            ) as http_client:
                async with streamable_http_client(
                    self.url,
                    http_client=http_client,
                    terminate_on_close=False,
                ) as (read_stream, write_stream, _get_session_id):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(
                            seconds=self._settings.backend_read_timeout_seconds
                        ),
                    ) as session:
                        with bound_contextvars(**initial_log_context):
                            await session.initialize()
                            self.tools = tuple((await session.list_tools()).tools)
                            self.resources = tuple((await session.list_resources()).resources)
                        if self._ready is not None and not self._ready.done():
                            self._ready.set_result(None)
                        await self._serve(session)
        except BaseException as error:
            actionable = _actionable_cleanup_error(error)
            failure = actionable or RuntimeError("Main Sequence MCP owner task was cancelled")
            self._failure = failure
        finally:
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(
                    failure or RuntimeError("Main Sequence MCP client failed to start")
                )
            self._fail_pending_commands(failure or RuntimeError("Main Sequence MCP client closed"))

    async def _serve(self, session: ClientSession) -> None:
        commands = self._commands
        if commands is None:
            raise RuntimeError("Main Sequence MCP command queue is not initialized")
        while True:
            command = await commands.get()
            if isinstance(command, _CloseCommand):
                return
            if isinstance(command, _CallToolCommand):
                try:
                    with bound_contextvars(**command.log_context):
                        tool_result = await session.call_tool(
                            command.name,
                            command.arguments,
                        )
                except Exception as error:
                    if not command.result.done():
                        command.result.set_exception(error)
                else:
                    if not command.result.done():
                        command.result.set_result(tool_result)
            else:
                try:
                    with bound_contextvars(**command.log_context):
                        resource_result = await session.read_resource(AnyUrl(command.uri))
                except Exception as error:
                    if not command.result.done():
                        command.result.set_exception(error)
                else:
                    if not command.result.done():
                        command.result.set_result(resource_result)

    def _fail_pending_commands(self, error: Exception) -> None:
        if self._commands is None:
            return
        while not self._commands.empty():
            command = self._commands.get_nowait()
            if not isinstance(command, _CloseCommand) and not command.result.done():
                command.result.set_exception(error)

    def _require_commands(self) -> asyncio.Queue[_MCPCommand]:
        if self._closed:
            raise RuntimeError("Main Sequence MCP client is closed")
        if self._owner_task is None or self._commands is None:
            raise RuntimeError("Main Sequence MCP client is not connected")
        if self._owner_task.done():
            raise RuntimeError("Main Sequence MCP owner task stopped") from self._failure
        return self._commands

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> types.CallToolResult:
        commands = self._require_commands()
        result: asyncio.Future[types.CallToolResult] = asyncio.get_running_loop().create_future()
        commands.put_nowait(
            _CallToolCommand(
                name=name,
                arguments=arguments,
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
