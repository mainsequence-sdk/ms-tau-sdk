from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ms_tau_sdk.app import create_app
from ms_tau_sdk.backend.assertions import (
    CALLER_ASSERTION_TYPE,
    PLATFORM_ASSERTION_TYPE,
    jwk_thumbprint,
)
from ms_tau_sdk.settings import TauSDKSettings

# Gateway-verified caller identity headers (ADR-28 amendment 2). Protected
# message routes reject requests without them; tests that exercise those routes
# pass `headers=USER_CALLER_HEADERS` (or the agent variant) to `asgi_client`, or
# per request. The shared client sends no identity by default.
USER_CALLER_HEADERS = {
    "X-Caller-Kind": "user",
    "X-User-UID": "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d",
    "X-Username": "jose",
}
AGENT_CALLER_HEADERS = {
    "X-Caller-Kind": "agent",
    "X-User-UID": "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d",
    "X-Username": "jose",
    "X-Caller-Agent-UID": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "X-Caller-Coding-Agent-Service-UID": "f0e1d2c3-b4a5-4968-8776-655443322110",
    "X-Caller-Agent-Session-UID": "11111111-2222-4333-8444-555555555555",
}


# The variables the platform sets when it hosts a runtime. Any of the first five switches the SDK
# to hosted caller authentication, so no test inherits them from the environment that runs it.
HOSTED_ENVIRONMENT = (
    "MAINSEQUENCE_CALLER_AUTH_MODE",
    "APP_NAME",
    "FASTAPI_PUBLIC_BASE_URL",
    "MAINSEQUENCE_CALLER_ASSERTION_ISSUER",
    "MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL",
    "MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID",
)


@pytest.fixture(autouse=True)
def no_hosted_environment(monkeypatch) -> None:
    for name in HOSTED_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolated_tau_state_root(monkeypatch, tmp_path_factory) -> None:
    """Keep Tau runtime state out of the package tree and the developer's home.

    `MAINSEQUENCE_TAU_STATE_ROOT` defaults to an XDG-style user directory, so a
    test that builds settings without naming a root would otherwise write real
    runtime state there.
    """
    monkeypatch.setenv(
        "MAINSEQUENCE_TAU_STATE_ROOT",
        str(tmp_path_factory.mktemp("tau-state")),
    )


type MCPCallAnswer = Callable[[dict[str, Any]], Awaitable[httpx.Response]]
type MCPOpenAnswer = Callable[[str, list[str]], httpx.Response | None]


@pytest.fixture
def mcp_server(monkeypatch) -> Callable[..., list[str]]:
    """Serve the SDK's MCP connections from a stateless MCP server that answers in plain JSON.

    The real MCP client library talks to it over HTTP, so a status or a delay reaches the SDK the
    way a gateway's would. ``on_call`` answers each ``tools/call``; ``on_open``, given the method
    and the methods received so far, may replace the answer to a request that opens the session.
    The returned list records each method the server receives.
    """

    real_client = httpx.AsyncClient

    def serve(
        *,
        tools: Sequence[dict[str, Any]] = (),
        on_call: MCPCallAnswer,
        on_open: MCPOpenAnswer | None = None,
    ) -> list[str]:
        received: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            message = json.loads(request.content)
            method = message.get("method", "")
            received.append(method)
            if "id" not in message:
                return httpx.Response(202)
            if method == "tools/call":
                return await on_call(message)
            if on_open is not None and (answer := on_open(method, received)) is not None:
                return answer
            if method == "initialize":
                result: dict[str, Any] = {
                    "protocolVersion": message["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "1"},
                }
            else:
                result = {"tools": list(tools)}
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
            )

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
        )
        return received

    return serve


@pytest.fixture
def runtime_identity_token_file(tmp_path_factory) -> Path:
    """A stand-in for the projected workload identity token file, outside every workspace."""
    token_file = tmp_path_factory.mktemp("runtime-identity") / "token"
    token_file.write_text("dummy-projected-workload-identity-token", encoding="utf-8")
    return token_file


@pytest.fixture
def test_settings(tmp_path, runtime_identity_token_file: Path) -> TauSDKSettings:
    return TauSDKSettings(
        _env_file=None,
        backend_url="http://backend:8000",
        runtime_credential_id="credential-id",
        runtime_identity_token_file=runtime_identity_token_file,
        workspace=tmp_path,
        startup_dependencies_enabled=False,
    )


@pytest.fixture
def sdk_app(test_settings: TauSDKSettings) -> FastAPI:
    return create_app(test_settings)


@asynccontextmanager
async def _asgi_client(
    app: Any,
    *,
    lifespan: bool = False,
    raise_app_exceptions: bool = True,
    headers: dict[str, str] | None = None,
) -> AsyncIterator[AsyncClient]:
    async def client_context() -> AsyncIterator[AsyncClient]:
        transport = ASGITransport(
            app=app,
            raise_app_exceptions=raise_app_exceptions,
        )
        async with AsyncClient(
            transport=transport, base_url="http://test", headers=dict(headers or {})
        ) as client:
            yield client

    if lifespan:
        async with app.router.lifespan_context(app):
            async for client in client_context():
                yield client
        return

    async for client in client_context():
        yield client


@pytest.fixture
def asgi_client() -> Callable[..., Any]:
    return _asgi_client


@pytest.fixture
async def sdk_client(sdk_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _asgi_client(sdk_app, lifespan=True) as client:
        yield client


class PlatformKeys:
    """The platform's side of request identity: it signs assertions and serves the key set."""

    issuer = "https://platform.test"
    jwks_url = "https://platform.test/fastapi/caller-keys/"
    release_uid = "6f1c2b8e-4d3a-4e5f-9a8b-7c6d5e4f3a2b"
    environment_uid = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
    user_uid = "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d"

    def __init__(self) -> None:
        self.signing_key = Ed25519PrivateKey.generate()
        self.published = [self.signing_key]
        self.fetches = 0
        self.status_code = 200
        self.cache_control: str | None = "public, max-age=60"
        self.failure: Exception | None = None

    @staticmethod
    def public_x(key: Ed25519PrivateKey) -> str:
        raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @classmethod
    def kid(cls, key: Ed25519PrivateKey) -> str:
        return jwk_thumbprint(cls.public_x(key))

    @classmethod
    def jwk(cls, key: Ed25519PrivateKey) -> dict[str, str]:
        return {
            "kid": cls.kid(key),
            "alg": "EdDSA",
            "use": "sig",
            "kty": "OKP",
            "crv": "Ed25519",
            "x": cls.public_x(key),
        }

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.jwk(key) for key in self.published]}

    def respond(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == self.jwks_url
        self.fetches += 1
        if self.failure is not None:
            raise self.failure
        headers = {"Cache-Control": self.cache_control} if self.cache_control else {}
        return httpx.Response(self.status_code, json=self.jwks(), headers=headers)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.respond)

    def platform_claims(self, *, now: int | None = None, **overrides: Any) -> dict[str, Any]:
        issued_at = int(time.time()) if now is None else now
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": f"urn:mainsequence:fapi:{self.release_uid}",
            "resource_release_uid": self.release_uid,
            "organization_environment_uid": self.environment_uid,
            "iat": issued_at,
            "nbf": issued_at,
            "exp": issued_at + 300,
        }
        claims.update(overrides)
        return claims

    def caller_claims(
        self,
        *,
        user_uid: str | None = None,
        team_uids: list[str] | None = None,
        is_organization_admin: bool = False,
        now: int | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        claims = self.platform_claims(now=now)
        claims.update(
            sub=user_uid or self.user_uid,
            team_uids=[] if team_uids is None else team_uids,
            is_organization_admin=is_organization_admin,
        )
        claims.update(overrides)
        return claims

    def sign(
        self,
        claims: dict[str, Any],
        *,
        typ: str = CALLER_ASSERTION_TYPE,
        key: Ed25519PrivateKey | None = None,
        kid: str | None = None,
    ) -> str:
        signing_key = key or self.signing_key
        return jwt.encode(
            claims,
            signing_key,
            algorithm="EdDSA",
            headers={"kid": kid or self.kid(signing_key), "typ": typ},
        )

    def caller_assertion(self, **claims: Any) -> str:
        return self.sign(self.caller_claims(**claims))

    def platform_assertion(self, **claims: Any) -> str:
        return self.sign(self.platform_claims(**claims), typ=PLATFORM_ASSERTION_TYPE)

    def hosted_settings(self, workspace: Path, **overrides: Any) -> TauSDKSettings:
        values: dict[str, Any] = {
            "backend_url": "http://backend:8000",
            "runtime_credential_id": "credential-id",
            "workspace": workspace,
            "startup_dependencies_enabled": False,
            "caller_assertion_issuer": self.issuer,
            "caller_assertion_jwks_url": self.jwks_url,
            "resource_release_uid": self.release_uid,
            "organization_environment_uid": self.environment_uid,
        }
        values.update(overrides)
        return TauSDKSettings(_env_file=None, **values)


@pytest.fixture
def platform_keys() -> PlatformKeys:
    return PlatformKeys()


@pytest.fixture
def served_platform_keys(platform_keys: PlatformKeys) -> Iterator[PlatformKeys]:
    """Serve the platform's key set at its HTTPS URL to every HTTPX client of the test."""

    with respx.mock(assert_all_called=False) as router:
        router.get(platform_keys.jwks_url).mock(side_effect=platform_keys.respond)
        yield platform_keys
