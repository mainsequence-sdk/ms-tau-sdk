"""FastAPI application and Uvicorn entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from astro import __version__
from astro.api import a2a, chat, health, llm, providers, sessions
from astro.backend.auth import RuntimeCredentialAuth
from astro.backend.client import MainSequenceClient
from astro.errors import AstroError
from astro.logging import RequestContextMiddleware, configure_logging
from astro.providers.factory import ProviderFactory
from astro.runtime.manager import SessionRuntimeManager
from astro.settings import Settings, get_settings

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(
        resolved.log_level,
        machine_sink=resolved.log_machine_sink,
        human_sink=resolved.log_human_sink,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved.validate_runtime_auth()
        auth = RuntimeCredentialAuth(resolved)
        backend_client = MainSequenceClient(resolved, auth)
        provider_factory = ProviderFactory(backend_client)
        manager = SessionRuntimeManager(
            settings=resolved,
            backend=backend_client,
            providers=provider_factory,
        )
        app.state.settings = resolved
        app.state.backend = backend_client
        app.state.provider_factory = provider_factory
        app.state.runtime_manager = manager
        await manager.start()
        logger.info(
            "astro.service.started",
            runtime="tau",
            version=__version__,
        )
        try:
            yield
        finally:
            logger.info("astro.service.stopping", runtime="tau")
            await manager.aclose()
            await backend_client.aclose()
            logger.info("astro.service.stopped", runtime="tau")

    app = FastAPI(
        title="Main Sequence Astro",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    if resolved.trusted_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.trusted_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(AstroError)
    async def astro_error_handler(request: Request, error: AstroError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.status_code,
            content={
                "ok": False,
                "error": error.code,
                "message": str(error),
                "detail": error.detail,
            },
        )

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(llm.router)
    app.include_router(a2a.router)
    app.include_router(providers.router)
    app.include_router(sessions.router)
    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "astro.app:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
    )
