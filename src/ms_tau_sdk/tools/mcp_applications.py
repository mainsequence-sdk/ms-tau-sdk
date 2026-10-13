"""MCP endpoints of the applications an Agent declares (ADR 0021, section 4; ADR 0023).

Declaring an application registers its MCP; it grants no access. The platform resolves each
declared application in the Agent's Environment and hands the runtime its name and release at
startup. Each application gets four tools: ``<name>__list_tools`` and ``<name>__call_tool`` for its
tools, and ``<name>__list_resources`` and ``<name>__read_resource`` for the skills and resources it
publishes. They run inside the turn: the SDK obtains the application's address and a short-lived
token, opens an MCP session for the call, and closes it afterwards. The token carries the
delegation of the person the turn serves, when it serves one, and is the Agent's own otherwise; the
application decides what the call may do. No session, token or catalog is shared between turns or
people, and nothing is read when the session loads, because no turn is running then.

Each tool declares how long it may run in its ``_meta`` (ADR 0022); a tool that declares nothing
gets ``TAU_MCP_TOOL_TIMEOUT_SECONDS``. A failure reaches the model in plain words, and
opening the session is tried once more when it fails before the tool call is sent.

A file of a skill the application lists (MCP Skills extension) reaches the model only when its
bytes match the size and SHA-256 digest the listing gives. Published skills are the application's
content: the SDK never adds them to the project's skills and grants nothing they ask for.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import AsyncGenerator, Mapping, Sequence
from typing import Protocol

import httpx
import httpx2
import structlog
from mcp import MCPError, types
from tau_agent.messages import TextContent
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue

from ms_tau_sdk.backend.mcp import (
    SKILLS_EXTENSION,
    MCPConnectionClient,
    Skill,
    SkillFile,
    raise_for_renewed_401,
)
from ms_tau_sdk.backend.models import MCPApplication
from ms_tau_sdk.errors import (
    ApplicationReadinessTimeoutError,
    ApplicationWakingError,
    BackendError,
    BackendTimeoutError,
    ConfigurationError,
)
from ms_tau_sdk.runtime.requester import (
    RequesterBindingError,
    _turn_application_access,
    _TurnApplicationAccess,
)
from ms_tau_sdk.settings import TauSDKSettings
from ms_tau_sdk.tools.mcp_connection import TIMEOUT_META_KEY as TIMEOUT_META_KEY
from ms_tau_sdk.tools.mcp_connection import declared_timeout, mcp_tool_result

logger = structlog.get_logger(__name__)

_APPLICATION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_RESERVED_NAMES = frozenset({"mainsequence"})


class ApplicationConnector(Protocol):
    async def __call__(
        self, application: MCPApplication, *, signal: ToolCancellationToken | None = None
    ) -> MCPConnectionClient: ...


class _ApplicationTokenAuth(httpx2.Auth):
    """Send the turn's application token; obtain a new one once when the application says 401."""

    def __init__(self, access: _TurnApplicationAccess) -> None:
        self._token = access.token
        self._renew = access.renew

    async def async_auth_flow(
        self,
        request: httpx2.Request,
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        request.headers["Authorization"] = f"Bearer {self._token}"
        response = yield request
        if response.status_code == 401:
            self._token = await self._renew()
            request.headers["Authorization"] = f"Bearer {self._token}"
            raise_for_renewed_401((yield request))


def application_connector(settings: TauSDKSettings) -> ApplicationConnector:
    """Open an MCP session to one application for the current turn."""

    async def connect(
        application: MCPApplication, *, signal: ToolCancellationToken | None = None
    ) -> MCPConnectionClient:
        access = await _turn_application_access(
            application.resource_release_uid,
            ready_timeout_seconds=settings.mcp_application_ready_timeout_seconds,
            cancelled=signal.is_cancelled if signal is not None else None,
        )
        return await MCPConnectionClient(
            name=application.name,
            display_name=application.name,
            url=f"{str(access.rpc_url).rstrip('/')}/mcp",
            auth=_ApplicationTokenAuth(access),
            # The connection applies the tool's response deadline to its call; these HTTP limits
            # also bound initialization and catalog requests.
            timeout=httpx2.Timeout(
                connect=settings.backend_connect_timeout_seconds,
                read=settings.backend_read_timeout_seconds,
                write=settings.backend_write_timeout_seconds,
                pool=settings.backend_pool_timeout_seconds,
            ),
            read_timeout_seconds=settings.backend_read_timeout_seconds,
            read_concurrency=settings.mcp_read_concurrency,
            # An application declares the Skills extension only on the newer protocol revision.
            mode="auto",
        ).open()

    return connect


def _timed_out(error: BaseException) -> bool:
    if isinstance(error, MCPError):
        return error.code == types.REQUEST_TIMEOUT
    return isinstance(error, TimeoutError) or (
        isinstance(error, httpx.TimeoutException | httpx2.TimeoutException)
        and not isinstance(error, httpx.ConnectTimeout | httpx2.ConnectTimeout)
    )


def _failure(
    name: str,
    error: BaseException,
    *,
    tool: str | None,
    timeout_seconds: float,
) -> tuple[str, dict[str, JSONValue]]:
    """What the model reads about a failure, and its details; never the error's own text."""

    if isinstance(error, RequesterBindingError):
        return "Your access for this request ended.", {"failure": "access_ended"}
    if isinstance(error, ApplicationReadinessTimeoutError):
        return (
            f"The {name} application did not become ready within "
            f"{error.timeout_seconds:g} seconds. "
            "It is still starting; try again shortly.",
            {
                "failure": "readiness_timeout",
                "phase": "readiness",
                "runtime_access_state": "waking",
                "timeout_seconds": error.timeout_seconds,
            },
        )
    if isinstance(error, ApplicationWakingError):
        return (
            f"The {name} application is still starting; try again shortly.",
            {"failure": "not_ready", "phase": "readiness", "runtime_access_state": "waking"},
        )
    if isinstance(error, BackendError):
        if isinstance(error, BackendTimeoutError) or error.backend_status in {408, 504}:
            details: dict[str, JSONValue] = {"failure": "timeout", "phase": "access"}
            if isinstance(error, BackendTimeoutError) and error.timeout_seconds is not None:
                details["timeout_seconds"] = error.timeout_seconds
            if error.backend_status is not None:
                details["status"] = error.backend_status
            return (
                f"Main Sequence timed out while providing access to the {name} application.",
                details,
            )
        if error.backend_status in {403, 404}:
            return (
                f"The {name} application is not available for this call.",
                {"failure": "not_available"},
            )
        return (
            f"Main Sequence could not provide access to the {name} application.",
            {"failure": "access_unavailable"},
        )
    if _timed_out(error):
        subject = f"The {name} tool {tool}" if tool is not None else f"The {name} application"
        return (
            f"{subject} did not answer within {timeout_seconds:g} seconds.",
            {"failure": "timeout", "timeout_seconds": timeout_seconds},
        )
    if isinstance(error, httpx.HTTPStatusError | httpx2.HTTPStatusError):
        status = error.response.status_code
        if status in {408, 504}:
            text = f"The {name} application or its gateway reported a timeout ({status})."
            return text, {"failure": "timeout", "status": status}
        if status >= 500:
            text = f"The {name} application answered with a server error ({status})."
            return text, {"failure": "server_error", "status": status}
        if status in {401, 403}:
            text = f"The {name} application refused this call ({status})."
            return text, {"failure": "refused", "status": status}
        text = f"The {name} application answered with an error ({status})."
        return text, {"failure": "error_status", "status": status}
    if isinstance(
        error,
        httpx.ConnectError | httpx.ConnectTimeout | httpx2.ConnectError | httpx2.ConnectTimeout,
    ):
        return f"The {name} application could not be reached.", {"failure": "unreachable"}
    return f"The {name} application could not complete this call.", {"failure": "failed"}


def _retryable(error: BaseException) -> bool:
    """Whether opening the session failed in a way that a second attempt may not repeat."""

    if isinstance(error, httpx.HTTPStatusError | httpx2.HTTPStatusError):
        return error.response.status_code == 408 or error.response.status_code >= 500
    return isinstance(error, httpx.TransportError | httpx2.TransportError) or _timed_out(error)


def _error(name: str, text: str, **details: JSONValue) -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text=text)],
        details={"application": name, "is_error": True, **details},
    )


def _tool_entry(tool: types.Tool) -> dict[str, JSONValue]:
    entry: dict[str, JSONValue] = {"name": tool.name, "inputSchema": dict(tool.input_schema)}
    if tool.description:
        entry["description"] = tool.description
    if tool.annotations is not None and tool.annotations.read_only_hint is not None:
        entry["readOnly"] = tool.annotations.read_only_hint
    return entry


def _owner(skills: Sequence[Skill], uri: str) -> Skill | None:
    """The listed skill whose directory holds ``uri``; the innermost one when skills nest."""

    owners = [skill for skill in skills if uri.startswith(skill.uri.removesuffix("SKILL.md"))]
    return max(owners, key=lambda skill: len(skill.uri), default=None)


def _listed_by_skill(skills: Sequence[Skill], uri: str) -> bool:
    """Whether ``uri`` is read as a file of a listed skill rather than as a plain resource.

    A skill with a file list owns its whole directory. A generated (``"dynamic"``) skill lists no
    files, so only its ``SKILL.md`` is its own.
    """

    owner = _owner(skills, uri)
    return owner is not None and (owner.resources != "dynamic" or uri == owner.uri)


def _skill_entry(skill: Skill) -> dict[str, JSONValue]:
    entry: dict[str, JSONValue] = {
        "uri": skill.uri,
        "name": skill.frontmatter["name"],
        "description": skill.frontmatter["description"],
    }
    if skill.resources != "dynamic":
        entry["files"] = [{"uri": file.uri, "size": file.size} for file in skill.resources]
    return entry


def _resource_entry(resource: types.Resource) -> dict[str, JSONValue]:
    entry: dict[str, JSONValue] = {"uri": str(resource.uri), "name": resource.name}
    if resource.description:
        entry["description"] = resource.description
    if resource.mime_type:
        entry["mimeType"] = resource.mime_type
    return entry


def _matches(
    contents: Sequence[types.TextResourceContents | types.BlobResourceContents],
    listed: SkillFile,
) -> bool:
    """Whether a read returned exactly one content, with the bytes the skill lists."""

    if len(contents) != 1:
        return False
    content = contents[0]
    if isinstance(content, types.TextResourceContents):
        data = content.text.encode()
    else:
        try:
            data = base64.b64decode(content.blob, validate=True)
        except binascii.Error:
            return False
    return (
        len(data) == listed.size and f"sha256:{hashlib.sha256(data).hexdigest()}" == listed.digest
    )


def _content_text(content: types.TextResourceContents | types.BlobResourceContents) -> str:
    if isinstance(content, types.TextResourceContents):
        return content.text
    return f"(Binary content of type {content.mime_type or 'unknown'}; not shown.)"


def _application_tools(
    application: MCPApplication,
    *,
    connect: ApplicationConnector,
    tool_timeout_seconds: float,
    open_timeout_seconds: float,
) -> list[AgentTool]:
    name = application.name

    async def open_session(signal: ToolCancellationToken | None) -> MCPConnectionClient:
        # Nothing has reached a tool while the session opens, so one more attempt is safe.
        try:
            return await connect(application, signal=signal)
        except Exception as error:
            if not _retryable(error) or (signal is not None and signal.is_cancelled()):
                raise
            logger.info(
                "runtime.mcp_application.retried",
                application=name,
                error_type=type(error).__name__,
            )
        return await connect(application, signal=signal)

    def failed(
        operation: str,
        error: BaseException,
        *,
        mcp_tool: str | None = None,
        call_timeout_seconds: float | None = None,
    ) -> AgentToolResult:
        # A failure after the call was sent, which has a time limit by then, is the tool's;
        # a failure while the session opens is the application's.
        text, failure = _failure(
            name,
            error,
            tool=mcp_tool if call_timeout_seconds is not None else None,
            timeout_seconds=(
                call_timeout_seconds if call_timeout_seconds is not None else open_timeout_seconds
            ),
        )
        if mcp_tool is not None:
            failure["mcp_tool"] = mcp_tool
        logger.warning(
            "runtime.mcp_application.failed",
            application=name,
            operation=operation,
            error_type=type(error).__name__,
            **failure,
        )
        return _error(name, text, **failure)

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
            client = await open_session(signal)
            try:
                tools = client.tools
                publishes_skills = SKILLS_EXTENSION in client.extensions
                publishes_resources = bool(client.resources)
            finally:
                await client.aclose()
        except Exception as error:
            return failed("list_tools", error)
        entries = [_tool_entry(tool) for tool in tools]
        published = ""
        if publishes_skills:
            published = (
                " It also publishes skills, instructions for using its tools: list them with "
                f"{name}__list_resources."
            )
        elif publishes_resources:
            published = f" It also publishes resources: list them with {name}__list_resources."
        return AgentToolResult(
            content=[
                TextContent(
                    text=f"Tools of the {name} application. Call one with {name}__call_tool."
                    + published
                    + "\n"
                    + json.dumps(entries, indent=2, sort_keys=True, default=str)
                )
            ],
            details={"application": name, "is_error": False, "tools": [t.name for t in tools]},
        )

    async def list_resources(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, arguments, on_update
        if signal is not None and signal.is_cancelled():
            return _error(name, f"Listing the {name} resources was cancelled.", cancelled=True)
        try:
            client = await open_session(signal)
            try:
                skills = await client.list_skills()
                resources = client.resources
            finally:
                await client.aclose()
        except Exception as error:
            return failed("list_resources", error)
        listing: dict[str, list[dict[str, JSONValue]]] = {
            "skills": [_skill_entry(skill) for skill in skills],
            "resources": [
                _resource_entry(resource)
                for resource in resources
                if not _listed_by_skill(skills, str(resource.uri))
            ],
        }
        if listing["skills"] or listing["resources"]:
            text = (
                f"Skills and resources the {name} application publishes. A skill is the "
                f"application's instructions for using its tools. Read a skill file or a resource "
                f"with {name}__read_resource.\n"
                + json.dumps(listing, indent=2, sort_keys=True, default=str)
            )
        else:
            text = f"The {name} application publishes no skills or resources."
        return AgentToolResult(
            content=[TextContent(text=text)],
            details={
                "application": name,
                "is_error": False,
                "skills": [skill.uri for skill in skills],
                "resources": [entry["uri"] for entry in listing["resources"]],
            },
        )

    async def read_resource(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, on_update
        uri = arguments.get("uri")
        if not isinstance(uri, str) or not uri:
            return _error(name, f"Name the {name} skill file or resource to read in `uri`.")
        if signal is not None and signal.is_cancelled():
            return _error(name, f"Reading the {name} resource was cancelled.", cancelled=True)
        result: types.ReadResourceResult | None = None
        owner: Skill | None = None
        listed: SkillFile | None = None
        try:
            client = await open_session(signal)
            try:
                # Only what the application lists is read: a file of a listed skill, which must
                # match the skill's list, or an advertised resource.
                owner = _owner(await client.list_skills(), uri)
                if owner is not None and owner.resources != "dynamic":
                    listed = next((file for file in owner.resources if file.uri == uri), None)
                    readable = listed is not None
                else:
                    readable = (owner is not None and uri == owner.uri) or any(
                        str(resource.uri) == uri for resource in client.resources
                    )
                if readable:
                    result = await client.read_resource(uri)
            finally:
                await client.aclose()
        except Exception as error:
            return failed("read_resource", error)
        if result is None:
            if owner is not None and owner.resources != "dynamic":
                return _error(
                    name,
                    f"{uri} is not a file of the {name} skill {owner.frontmatter['name']} as the "
                    f"application lists it. List its files with {name}__list_resources.",
                    uri=uri,
                    failure="not_listed",
                )
            return _error(
                name,
                f"The {name} application does not publish {uri}. List what it publishes with "
                f"{name}__list_resources.",
                uri=uri,
                failure="not_published",
            )
        contents = result.contents
        if listed is not None:
            contents = [content for content in contents if str(content.uri) == uri]
            if not _matches(contents, listed):
                logger.warning(
                    "runtime.mcp_application.failed",
                    application=name,
                    operation="read_resource",
                    failure="unverified",
                )
                return _error(
                    name,
                    f"The {name} application served {uri} with other content than its skill "
                    f"lists, so it is not shown. List the skill again with {name}__list_resources.",
                    uri=uri,
                    failure="unverified",
                )
        origin = f"From the {name} application: {uri}"
        details: dict[str, JSONValue] = {"application": name, "is_error": False, "uri": uri}
        if owner is not None:
            origin += f", a file of its skill {owner.frontmatter['name']}"
            details |= {"skill": owner.uri, "verified": listed is not None}
        return AgentToolResult(
            content=[
                TextContent(text=f"{origin}.\n\n"),
                *(TextContent(text=_content_text(content)) for content in contents),
            ],
            details=details,
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
        call_timeout_seconds: float | None = None
        try:
            client = await open_session(signal)
            try:
                declared = next(
                    (
                        declared_timeout(
                            tool,
                            invalid_event="runtime.mcp_application.invalid_timeout",
                            application=name,
                        )
                        for tool in client.tools
                        if tool.name == tool_name
                    ),
                    None,
                )
                call_timeout_seconds = declared if declared is not None else tool_timeout_seconds
                result = await client.call_tool(
                    tool_name, dict(tool_arguments), timeout_seconds=call_timeout_seconds
                )
            finally:
                await client.aclose()
        except Exception as error:
            return failed(
                "call_tool", error, mcp_tool=tool_name, call_timeout_seconds=call_timeout_seconds
            )
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
        AgentTool(
            name=f"{name}__list_resources",
            label=f"List {name} skills and resources",
            description=(
                f"List the skills and resources the {name} application publishes. A skill is the "
                "application's instructions for using its tools; each lists its files."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            execute_fn=list_resources,
            execution_mode="parallel",
        ),
        AgentTool(
            name=f"{name}__read_resource",
            label=f"Read a {name} skill file or resource",
            description=(
                f"Read one skill file or resource the {name} application publishes, by its URI. "
                f"Use {name}__list_resources first to see what it publishes."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "uri": {
                        "type": "string",
                        "description": f"The URI of a {name} skill file or resource.",
                    },
                },
                "required": ["uri"],
                "additionalProperties": False,
            },
            execute_fn=read_resource,
            execution_mode="parallel",
        ),
    ]


def create_mcp_application_tools(
    applications: Sequence[MCPApplication],
    *,
    connect: ApplicationConnector,
    tool_timeout_seconds: float,
    open_timeout_seconds: float,
) -> list[AgentTool]:
    """Four tools for each declared application, in declaration order.

    ``tool_timeout_seconds`` bounds a call to a tool that declares no limit of its own;
    ``open_timeout_seconds`` is the wait for each request that opens the session.
    """

    tools: list[AgentTool] = []
    seen: set[str] = set()
    for application in applications:
        name = application.name
        if not _APPLICATION_NAME.fullmatch(name) or name in _RESERVED_NAMES:
            raise ConfigurationError(f"Invalid MCP application name: {name!r}")
        if name in seen:
            raise ConfigurationError(f"Duplicate MCP application name: {name!r}")
        seen.add(name)
        tools.extend(
            _application_tools(
                application,
                connect=connect,
                tool_timeout_seconds=tool_timeout_seconds,
                open_timeout_seconds=open_timeout_seconds,
            )
        )
    return tools
