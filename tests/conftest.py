from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from astro.app import create_app
from astro.settings import Settings


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        backend_url="http://backend:8000",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
        project_root=tmp_path,
        startup_dependencies_enabled=False,
    )


@pytest.fixture
def astro_app(test_settings: Settings) -> FastAPI:
    return create_app(test_settings)


@asynccontextmanager
async def _asgi_client(
    app: Any,
    *,
    lifespan: bool = False,
    raise_app_exceptions: bool = True,
) -> AsyncIterator[AsyncClient]:
    async def client_context() -> AsyncIterator[AsyncClient]:
        transport = ASGITransport(
            app=app,
            raise_app_exceptions=raise_app_exceptions,
        )
        async with AsyncClient(transport=transport, base_url="http://test") as client:
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
async def astro_client(astro_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _asgi_client(astro_app, lifespan=True) as client:
        yield client
