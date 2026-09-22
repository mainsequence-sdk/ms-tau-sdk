"""Allowlisted, streaming HTTP bridge to one local Tau process."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from .config import Profile

MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
ALLOWED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GET", re.compile(r"/(?:health|ready|version)")),
    ("POST", re.compile(r"/api/chat")),
    ("GET", re.compile(r"/api/chat/session-model")),
    ("PUT", re.compile(r"/api/chat/session-model")),
    ("GET", re.compile(r"/api/chat/model-providers")),
    ("POST", re.compile(r"/api/chat/session/cancel")),
    ("POST", re.compile(r"/api/a2a/v1/message:(?:send|stream)")),
    ("GET", re.compile(r"/api/a2a/v1/tasks")),
    ("GET", re.compile(r"/api/a2a/v1/tasks/[A-Za-z0-9._-]+")),
    ("GET", re.compile(r"/api/a2a/v1/tasks/[A-Za-z0-9._-]+:subscribe")),
    ("POST", re.compile(r"/api/a2a/v1/tasks/[A-Za-z0-9._-]+:cancel")),
    ("GET", re.compile(r"/api/a2a/v1/extendedAgentCard")),
)


class TauUnavailable(ValueError):
    """Selected endpoint is unavailable or is not a local Tau runtime."""


def allowed(method: str, path: str) -> bool:
    return any(method == verb and pattern.fullmatch(path) for verb, pattern in ALLOWED)


async def probe(client: httpx.AsyncClient, profile: Profile) -> dict[str, Any]:
    try:
        response = await client.get(profile.url + "/health", timeout=5, follow_redirects=False)
        response.raise_for_status()
        health = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise TauUnavailable(f"Cannot reach local Tau: {type(error).__name__}") from error
    if not isinstance(health, dict) or health.get("runtime") != "tau":
        raise TauUnavailable("Selected endpoint is not a Tau runtime")
    if health.get("mode") != "local":
        raise TauUnavailable("Tau Board requires a local-mode Tau endpoint")
    return health


async def forward(
    request: Request,
    *,
    client: httpx.AsyncClient,
    profile: Profile,
) -> Response:
    path = "/" + request.path_params["path"]
    if not allowed(request.method, path):
        return Response("Tau route is not available through the board", status_code=404)
    await probe(client, profile)
    body = await request.body()
    if len(body) > MAX_REQUEST_BYTES:
        return Response("Request is too large", status_code=413)
    query = request.url.query
    url = profile.url + path + ("?" + query if query else "")
    headers: dict[str, str] = {}
    if body:
        headers["Content-Type"] = "application/json"
    if request.headers.get("a2a-extensions") and path.startswith("/api/a2a/v1/"):
        headers["A2A-Extensions"] = request.headers["a2a-extensions"][:512]
    try:
        upstream_request = client.build_request(request.method, url, content=body, headers=headers)
        upstream = await client.send(upstream_request, stream=True, follow_redirects=False)
    except httpx.HTTPError as error:
        raise TauUnavailable(f"Tau request failed: {type(error).__name__}") from error
    if 300 <= upstream.status_code < 400:
        await upstream.aclose()
        raise TauUnavailable("Tau endpoint redirected a board request")
    response_headers = {
        name: upstream.headers[name]
        for name in ("x-agent-session-uid", "cache-control", "x-vercel-ai-ui-message-stream")
        if name in upstream.headers
    }
    media_type = upstream.headers.get("content-type", "application/json").split(";", 1)[0]
    if media_type == "text/event-stream":

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            chunks(),
            status_code=upstream.status_code,
            media_type="text/event-stream",
            headers=response_headers,
        )
    try:
        payload = await upstream.aread()
    finally:
        await upstream.aclose()
    if len(payload) > MAX_RESPONSE_BYTES:
        return Response("Tau response is too large", status_code=502)
    return Response(
        payload,
        status_code=upstream.status_code,
        media_type=media_type,
        headers=response_headers,
    )
