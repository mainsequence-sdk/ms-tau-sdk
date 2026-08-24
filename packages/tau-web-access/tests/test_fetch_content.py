import asyncio

import httpx
import pytest

from tau_web_access import WebAccessSettings, create_web_tools
from tau_web_access.extractors import ContentExtractor, _communicate


@pytest.mark.asyncio
async def test_local_fetch_is_stored_and_retrievable(tmp_path):
    (tmp_path / "guide.md").write_text("# Guide\n\nFull content", encoding="utf-8")
    async with httpx.AsyncClient() as client:
        tools = create_web_tools(
            settings=WebAccessSettings(allow_local_files=True),
            cwd=tmp_path,
            client=client,
        )
        fetch = next(tool for tool in tools if tool.name == "fetch_content")
        get_content = next(tool for tool in tools if tool.name == "get_search_content")

        result = await fetch.execute("fetch-1", {"url": "guide.md"})
        response_id = result.details["responseId"]
        stored = await get_content.execute(
            "get-1",
            {"responseId": response_id, "urlIndex": 0},
        )

    assert "# Guide" in result.text
    assert "# guide.md" in stored.text
    assert "Full content" in stored.text


@pytest.mark.asyncio
async def test_fetch_rejects_path_outside_root(tmp_path):
    async with httpx.AsyncClient() as client:
        tools = create_web_tools(
            settings=WebAccessSettings(allow_local_files=True),
            cwd=tmp_path,
            client=client,
        )
        fetch = next(tool for tool in tools if tool.name == "fetch_content")

        result = await fetch.execute("fetch-1", {"url": "/etc/hosts"})

    assert "Local files must be inside" in result.details["error"]


@pytest.mark.asyncio
async def test_cancelled_extractor_subprocess_is_terminated():
    class Process:
        def __init__(self):
            self.returncode = None
            self.started = asyncio.Event()
            self.terminated = asyncio.Event()

        async def communicate(self):
            self.started.set()
            await self.terminated.wait()
            return b"", b""

        def terminate(self):
            self.returncode = -15
            self.terminated.set()

        def kill(self):
            self.returncode = -9
            self.terminated.set()

    process = Process()
    task = asyncio.create_task(_communicate(process))  # type: ignore[arg-type]
    await process.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.returncode is not None


@pytest.mark.asyncio
async def test_remote_response_byte_limit_is_enforced(tmp_path, monkeypatch):
    async def allow_test_url(_url, *, allow_private_networks):
        del allow_private_networks

    monkeypatch.setattr(
        "tau_web_access.extractors.validate_public_url",
        allow_test_url,
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=b"x" * 11,
                headers={"content-type": "text/plain"},
            )
        )
    )
    extractor = ContentExtractor(
        settings=WebAccessSettings(max_response_bytes=10),
        client=client,
        cwd=tmp_path,
    )

    with pytest.raises(ValueError, match="byte limit"):
        await extractor.fetch("https://example.test/content.txt")

    await client.aclose()
