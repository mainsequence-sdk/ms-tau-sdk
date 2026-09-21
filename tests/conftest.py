from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ms_tau_sdk.app import create_app
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


@pytest.fixture
def test_settings(tmp_path) -> TauSDKSettings:
    return TauSDKSettings(
        _env_file=None,
        backend_url="http://backend:8000",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
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
