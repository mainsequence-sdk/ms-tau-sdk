import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from mcp import types

from astro.backend.mcp import (
    MainSequenceMCPClient,
    _RuntimeCredentialHTTPXAuth,
)
from astro.settings import Settings
from astro.tools.mainsequence_mcp import (
    create_mainsequence_mcp_tools,
    mainsequence_mcp_resource_prompt,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        backend_url="http://backend.test/",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
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
    resource_uri = "mainsequence://platform/skills/project-design"
    client = AsyncMock()
    client.tools = (
        types.Tool(
            name="project.list",
            description="List projects.",
            inputSchema={
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "additionalProperties": False,
            },
            annotations=types.ToolAnnotations(title="List projects"),
        ),
    )
    client.resources = (
        types.Resource(
            name="project_design",
            uri=resource_uri,
            description="Project design guidance.",
            mimeType="text/markdown",
        ),
    )
    client.call_tool.return_value = types.CallToolResult(
        content=[types.TextContent(type="text", text="project result")],
        structuredContent={"count": 1},
    )
    client.read_resource.return_value = types.ReadResourceResult(
        contents=[
            types.TextResourceContents(
                uri=resource_uri,
                mimeType="text/markdown",
                text="# Project design",
            )
        ]
    )

    tools = create_mainsequence_mcp_tools(client)

    assert [tool.name for tool in tools] == [
        "mainsequence__project_list",
        "mainsequence__read_resource",
    ]
    project_result = await tools[0].execute("call-1", {"limit": 5})
    assert project_result.text == "project result"
    assert project_result.details["structured_content"] == {"count": 1}
    client.call_tool.assert_awaited_once_with("project.list", {"limit": 5})

    resource_result = await tools[1].execute("call-2", {"uri": resource_uri})
    assert resource_result.text == "# Project design"
    client.read_resource.assert_awaited_once_with(resource_uri)

    prompt = mainsequence_mcp_resource_prompt(client)
    assert "mainsequence__read_resource" in prompt
    assert resource_uri in prompt


def test_normalized_mcp_tool_name_collisions_fail_session_setup():
    client = AsyncMock()
    client.tools = (
        types.Tool(name="project.list", inputSchema={"type": "object"}),
        types.Tool(name="project_list", inputSchema={"type": "object"}),
    )
    client.resources = ()

    with pytest.raises(ValueError, match="tool name collision"):
        create_mainsequence_mcp_tools(client)
