"""MCP endpoints of the applications an Agent declares (ADR 0021, section 4).

Declaring an application registers its MCP; it grants no access. The platform resolves each
declared application in the Agent's Environment and hands the runtime its name and release at
startup. Each application gets two tools, ``<name>__list_tools`` and ``<name>__call_tool``. Both run
inside the turn: the SDK obtains the application's address and a short-lived token, opens an MCP
session for the call, and closes it afterwards. The token carries the delegation of the person the
turn serves, when it serves one, and is the Agent's own otherwise; the application decides what the
call may do. No session, token or catalog is shared between turns or people, and nothing is read
when the session loads, because no turn is running then.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence

import httpx
import structlog
from mcp import types
from tau_agent.messages import TextContent
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue

from ms_tau_sdk.backend.mcp import MCPConnectionClient
from ms_tau_sdk.backend.models import MCPApplication
from ms_tau_sdk.errors import BackendError, ConfigurationError
from ms_tau_sdk.runtime.requester import (
    RequesterBindingError,
    _turn_application_access,
    _TurnApplicationAccess,
)
from ms_tau_sdk.settings import TauSDKSettings
from ms_tau_sdk.tools.mcp_connection import mcp_tool_result

logger = structlog.get_logger(__name__)

_APPLICATION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_RESERVED_NAMES = frozenset({"mainsequence"})

type ApplicationConnector = Callable[[MCPApplication, bool], Awaitable[MCPConnectionClient]]


class _ApplicationTokenAuth(httpx.Auth):
    """Send the turn's application token; obtain a new one once when the application says 401."""

    def __init__(self, access: _TurnApplicationAccess) -> None:
        self._token = access.token
        self._renew = access.renew

    async def async_auth_flow(
        self,
        request: httpx.Request,
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {self._token}"
        response = yield request
        if response.status_code == 401:
            self._token = await self._renew()
            request.headers["Authorization"] = f"Bearer {self._token}"
            yield request


def application_connector(settings: TauSDKSettings) -> ApplicationConnector:
    """Open an MCP session to one application for the current turn."""

    async def connect(application: MCPApplication, read_catalog: bool) -> MCPConnectionClient:
        access = await _turn_application_access(application.resource_release_uid)
        return await MCPConnectionClient(
            name=application.name,
            display_name=application.name,
            url=f"{str(access.rpc_url).rstrip('/')}/mcp",
            auth=_ApplicationTokenAuth(access),
            timeout=httpx.Timeout(
                connect=settings.backend_connect_timeout_seconds,
                read=settings.backend_read_timeout_seconds,
                write=settings.backend_write_timeout_seconds,
                pool=settings.backend_pool_timeout_seconds,
            ),
            read_timeout_seconds=settings.backend_read_timeout_seconds,
            read_concurrency=settings.mcp_read_concurrency,
            read_catalog=read_catalog,
        ).open()

    return connect


def _failure(name: str, error: BaseException) -> str:
    if isinstance(error, RequesterBindingError):
        return "Your access for this request ended."
    if isinstance(error, BackendError) and error.backend_status in {403, 404}:
        return f"The {name} application is not available for this call."
    return f"The {name} application could not be reached."


def _error(name: str, text: str, **details: JSONValue) -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text=text)],
        details={"application": name, "is_error": True, **details},
    )


def _tool_entry(tool: types.Tool) -> dict[str, JSONValue]:
    entry: dict[str, JSONValue] = {"name": tool.name, "inputSchema": dict(tool.inputSchema)}
    if tool.description:
        entry["description"] = tool.description
    if tool.annotations is not None and tool.annotations.readOnlyHint is not None:
        entry["readOnly"] = tool.annotations.readOnlyHint
    return entry


def _application_tools(
    application: MCPApplication,
    *,
    connect: ApplicationConnector,
) -> list[AgentTool]:
    name = application.name

    async def list_tools(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, arguments, on_update
        if signal is not None and signal.is_cancelled():
            return _error(name, f"Listing the {name} tools was cancelled.", cancelled=True)
        try:
            client = await connect(application, True)
            try:
                tools = client.tools
            finally:
                await client.aclose()
        except Exception as error:
            logger.warning(
                "runtime.mcp_application.failed",
                application=name,
                operation="list_tools",
                error_type=type(error).__name__,
            )
            return _error(name, _failure(name, error))
        entries = [_tool_entry(tool) for tool in tools]
        return AgentToolResult(
            content=[
                TextContent(
                    text=f"Tools of the {name} application. Call one with {name}__call_tool.\n"
                    + json.dumps(entries, indent=2, sort_keys=True, default=str)
                )
            ],
            details={"application": name, "is_error": False, "tools": [t.name for t in tools]},
        )

    async def call_tool(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, on_update
        tool_name = arguments.get("tool")
        tool_arguments = arguments.get("arguments") or {}
        if not isinstance(tool_name, str) or not tool_name:
            return _error(name, f"Name the {name} tool to call in `tool`.")
        if not isinstance(tool_arguments, dict):
            return _error(name, "`arguments` must be an object.")
        if signal is not None and signal.is_cancelled():
            return _error(name, f"The {name} tool call was cancelled.", cancelled=True)
        try:
            client = await connect(application, False)
            try:
                result = await client.call_tool(tool_name, dict(tool_arguments))
            finally:
                await client.aclose()
        except Exception as error:
            logger.warning(
                "runtime.mcp_application.failed",
                application=name,
                operation="call_tool",
                error_type=type(error).__name__,
            )
            return _error(name, _failure(name, error), mcp_tool=tool_name)
        projected = mcp_tool_result(canonical_name=tool_name, result=result, display_name=name)
        details = dict(projected.details) if isinstance(projected.details, dict) else {}
        details["application"] = name
        return AgentToolResult(content=projected.content, details=details)

    return [
        AgentTool(
            name=f"{name}__list_tools",
            label=f"List {name} tools",
            description=(
                f"List the tools of the {name} application: each tool's name, description and "
                "input schema."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            execute_fn=list_tools,
            execution_mode="parallel",
        ),
        AgentTool(
            name=f"{name}__call_tool",
            label=f"Call a {name} tool",
            description=(
                f"Call one tool of the {name} application by name. Use {name}__list_tools first "
                "to see the tools and their input schemas."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "description": f"The {name} tool's name."},
                    "arguments": {
                        "type": "object",
                        "description": "The tool's arguments, matching its input schema.",
                    },
                },
                "required": ["tool"],
                "additionalProperties": False,
            },
            execute_fn=call_tool,
            execution_mode="sequential",
        ),
    ]


def create_mcp_application_tools(
    applications: Sequence[MCPApplication],
    *,
    connect: ApplicationConnector,
) -> list[AgentTool]:
    """Two tools for each declared application, in declaration order."""

    tools: list[AgentTool] = []
    seen: set[str] = set()
    for application in applications:
        name = application.name
        if not _APPLICATION_NAME.fullmatch(name) or name in _RESERVED_NAMES:
            raise ConfigurationError(f"Invalid MCP application name: {name!r}")
        if name in seen:
            raise ConfigurationError(f"Duplicate MCP application name: {name!r}")
        seen.add(name)
        tools.extend(_application_tools(application, connect=connect))
    return tools
