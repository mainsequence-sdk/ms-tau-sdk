"""The model relay an application with its own hydrate endpoint serves (ADR 0030, section 8).

A hosted runtime holds a session pass, never a provider secret, and sends every model call to a
relay. The platform serves one for the credentials it holds. An application whose own hydrate
endpoint (ADR 0027) answers people's credentials serves this one, because only it holds those
secrets.

The application supplies two things: who a pass belongs to, and the provider, address and secret
for that session. Everything else is fixed here and behaves as the platform's relay does towards a
runtime:

- the pass is read where the runtime's transport puts a secret: an ``Authorization`` bearer
  token, ``x-api-key``, or the ``key`` query parameter. The real secret goes back in the place the
  pass arrived, and the pass is never forwarded;
- the upstream address comes only from the application's answer, never from the request;
- only the methods and paths the session's wire protocol uses are forwarded. A leading ``/v1`` in
  the request path is the transport's own;
- the provider's status, headers and body come back as they arrive, streamed. A redirect is
  returned and never followed;
- a refusal is ``403`` with a ``detail`` and reaches no provider; an unreachable provider is
  ``502``;
- the request body is read whole and is not capped here, because a call with images is large.
  The server in front of the relay sets the size limit;
- no pass, secret, request URL or path is logged. For Google the pass travels in the request URL,
  so the server and everything in front of it must not log URLs either.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import SecretStr

logger = structlog.get_logger(__name__)

RELAY_CONNECT_TIMEOUT_SECONDS = 10
RELAY_READ_TIMEOUT_SECONDS = 180
RELAY_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# The methods and paths each Tau wire protocol calls, after a leading /v1. The platform's relay
# allows the same ones.
RELAY_PATHS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "openai-completions": (("POST", r"/chat/completions"), ("POST", r"/responses")),
    "openai-responses": (("POST", r"/responses"), ("POST", r"/chat/completions")),
    "anthropic-messages": (("POST", r"/messages"),),
    "openai-codex-responses": (("POST", r"/codex/responses"), ("GET", r"/codex/models")),
    "google-generative-ai": (("POST", r"/models/[A-Za-z0-9._-]+:streamGenerateContent"),),
    "mistral-conversations": (("POST", r"/chat/completions"),),
}
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
# Never forwarded upstream: where a pass may sit, and what names the relay's own network.
_NOT_FORWARDED = _HOP_BY_HOP | frozenset(
    {
        "authorization",
        "x-api-key",
        "chatgpt-account-id",
        "host",
        "content-length",
        "cookie",
        "forwarded",
        "x-real-ip",
    }
)
_NOT_RETURNED = _HOP_BY_HOP | frozenset({"content-length", "set-cookie"})
# Set while a relayed call is served, so that httpx's own request line, which holds the URL and
# for Google the secret, is not logged.
_RELAYING: ContextVar[bool] = ContextVar("model_relay_call", default=False)


class _NoRelayedRequestLines(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _RELAYING.get()


_REQUEST_LINE_FILTER = _NoRelayedRequestLines()


class RelayRefused(Exception):
    """The call reaches no provider.

    Its message becomes the answer's ``detail``, so it must hold no pass and no secret.
    """


@dataclass(frozen=True, slots=True)
class RelayUpstream:
    """The provider call a session's pass stands for, as the application answers it.

    ``api`` is the session's wire protocol, a key of ``RELAY_PATHS``. ``base_url`` is the
    provider's own address. ``secret`` goes where the pass arrived; without one the pass is only
    removed. ``headers`` are added to every call, after the request's own. ``account_id`` is sent
    as ``chatgpt-account-id`` on ``openai-codex-responses``.
    """

    api: str
    base_url: str
    secret: SecretStr | None = None
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    account_id: str | None = None


type ResolvePass[SessionT] = Callable[[str], Awaitable[SessionT]]
type ResolveUpstream[SessionT] = Callable[[SessionT], Awaitable[RelayUpstream]]


@dataclass(frozen=True, slots=True)
class _ProviderCall:
    method: str
    url: str
    headers: dict[str, str] = field(repr=False)


def _refusal(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=403)


def _session_pass(
    headers: Mapping[str, str], query: Mapping[str, str]
) -> tuple[str, str] | tuple[None, None]:
    """Where the runtime's transport put the pass, and the pass, in the platform's order."""

    authorization = str(headers.get("authorization") or "")
    if authorization[:7].lower() == "bearer " and authorization[7:].strip():
        return "bearer", authorization[7:].strip()
    if headers.get("x-api-key"):
        return "x-api-key", str(headers["x-api-key"])
    if query.get("key"):
        return "query", str(query["key"])
    return None, None


def _provider_call(
    *,
    method: str,
    upstream_path: str,
    headers: Mapping[str, str],
    query: list[tuple[str, str]],
    place: str,
    upstream: RelayUpstream,
) -> _ProviderCall:
    """The provider request for one relayed call, or ``RelayRefused``."""

    allowed = RELAY_PATHS.get(upstream.api)
    if allowed is None:
        raise RelayRefused("The session's provider uses a protocol the relay does not serve.")
    parsed = urlsplit(upstream.base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RelayRefused("The provider's address is not one the relay calls.")
    suffix = "/" + upstream_path.lstrip("/")
    normalized = suffix[3:] if suffix.startswith("/v1/") else suffix
    if not any(
        method == allowed_method and re.fullmatch(pattern, normalized)
        for allowed_method, pattern in allowed
    ):
        raise RelayRefused("This path is not one the session's provider uses.")
    base_url = upstream.base_url.rstrip("/")
    url = base_url + (normalized if base_url.endswith("/v1") else suffix)

    forwarded = {
        name: value
        for name, value in headers.items()
        if name not in _NOT_FORWARDED and not name.startswith("x-forwarded-")
    }
    forwarded.update({str(name).lower(): str(value) for name, value in upstream.headers.items()})
    if upstream.api == "openai-codex-responses" and upstream.account_id:
        forwarded["chatgpt-account-id"] = upstream.account_id
    params = [(name, value) for name, value in query if name != "key"]
    if upstream.secret is not None:
        secret = upstream.secret.get_secret_value()
        if place == "x-api-key":
            forwarded["x-api-key"] = secret
        elif place == "query":
            params.append(("key", secret))
        else:
            forwarded["authorization"] = f"Bearer {secret}"
    if params:
        url = f"{url}?{urlencode(params)}"
    return _ProviderCall(method=method, url=url, headers=forwarded)


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(RELAY_READ_TIMEOUT_SECONDS, connect=RELAY_CONNECT_TIMEOUT_SECONDS),
        follow_redirects=False,
        trust_env=False,
    )


def create_model_relay_router[SessionT](
    *,
    resolve_pass: ResolvePass[SessionT],
    resolve_upstream: ResolveUpstream[SessionT],
    http: httpx.AsyncClient | None = None,
) -> APIRouter:
    """A router that serves the model relay at every path below where it is included.

    Include it under the address the hydrate endpoint answers in ``relay_definition.base_url``,
    for example ``app.include_router(router, prefix="/model-relay")``.

    ``resolve_pass`` answers the session a pass names and ``resolve_upstream`` the provider call
    for that session; either raises ``RelayRefused`` to refuse the call. ``http`` replaces the
    client the relay calls providers with; it must not follow redirects. The caller owns it.
    """

    for name in ("httpx", "httpcore"):
        if _REQUEST_LINE_FILTER not in logging.getLogger(name).filters:
            logging.getLogger(name).addFilter(_REQUEST_LINE_FILTER)
    router = APIRouter()

    @router.api_route("/{upstream_path:path}", methods=list(RELAY_METHODS))
    async def model_relay(request: Request, upstream_path: str) -> Response:
        started = time.monotonic()
        # Not capped: a call with images is larger than any framework default.
        body = await request.body()
        headers = {name.lower(): value for name, value in request.headers.items()}
        query = list(request.query_params.multi_items())
        method = request.method
        place, session_pass = _session_pass(headers, dict(query))
        try:
            if place is None or session_pass is None:
                raise RelayRefused("The call carries no session pass.")
            session = await resolve_pass(session_pass)
            upstream = await resolve_upstream(session)
            call = _provider_call(
                method=method,
                upstream_path=upstream_path,
                headers=headers,
                query=query,
                place=place,
                upstream=upstream,
            )
        except RelayRefused as refusal:
            logger.info("model_relay.call.refused", method=method)
            return _refusal(str(refusal))

        client = http or _http_client()
        relaying = _RELAYING.set(True)
        try:
            answer = await client.send(
                client.build_request(call.method, call.url, headers=call.headers, content=body),
                stream=True,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            # The error names the URL, which may hold the secret: it is neither logged nor kept.
            if http is None:
                await client.aclose()
            logger.warning(
                "model_relay.call.unreachable",
                api=upstream.api,
                method=method,
                duration_ms=round((time.monotonic() - started) * 1000, 3),
            )
            return JSONResponse({"detail": "The provider could not be reached."}, status_code=502)
        finally:
            _RELAYING.reset(relaying)

        async def stream() -> AsyncIterator[bytes]:
            size = 0
            try:
                async for chunk in answer.aiter_raw():
                    size += len(chunk)
                    yield chunk
            finally:
                await answer.aclose()
                if http is None:
                    await client.aclose()
                logger.info(
                    "model_relay.call.completed",
                    api=upstream.api,
                    method=method,
                    status_code=answer.status_code,
                    response_bytes=size,
                    duration_ms=round((time.monotonic() - started) * 1000, 3),
                )

        return StreamingResponse(
            stream(),
            status_code=answer.status_code,
            headers={
                name: value
                for name, value in answer.headers.items()
                if name.lower() not in _NOT_RETURNED
            },
        )

    return router
