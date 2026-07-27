"""Main Sequence MCP transport using Astro's existing backend authentication."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack
from datetime import timedelta

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

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
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
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
        timeout = httpx.Timeout(
            connect=self._settings.backend_connect_timeout_seconds,
            read=self._settings.backend_read_timeout_seconds,
            write=self._settings.backend_write_timeout_seconds,
            pool=self._settings.backend_pool_timeout_seconds,
        )
        http_client = await self._stack.enter_async_context(
            httpx.AsyncClient(
                timeout=timeout,
                auth=_RuntimeCredentialHTTPXAuth(self._auth),
            )
        )
        read_stream, write_stream, _get_session_id = await self._stack.enter_async_context(
            streamable_http_client(
                self.url,
                http_client=http_client,
                terminate_on_close=False,
            )
        )
        session = await self._stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=self._settings.backend_read_timeout_seconds),
            )
        )
        await session.initialize()
        self._session = session
        self.tools = tuple((await session.list_tools()).tools)
        self.resources = tuple((await session.list_resources()).resources)

    def _require_session(self) -> ClientSession:
        if self._session is None or self._closed:
            raise RuntimeError("Main Sequence MCP client is not connected")
        return self._session

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> types.CallToolResult:
        return await self._require_session().call_tool(name, arguments)

    async def read_resource(self, uri: str) -> types.ReadResourceResult:
        return await self._require_session().read_resource(AnyUrl(uri))

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._session = None
        await self._stack.aclose()
