"""FastAPI application and Uvicorn entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ms_tau_sdk import __version__
from ms_tau_sdk.api import a2a, chat, health, responses, sessions
from ms_tau_sdk.application import ApplicationServices
from ms_tau_sdk.errors import TauSDKError
from ms_tau_sdk.logging import RequestContextMiddleware, configure_logging
from ms_tau_sdk.settings import TauSDKSettings, get_settings

logger = structlog.get_logger(__name__)


def create_app(
    settings: TauSDKSettings | None = None,
    *,
    services_factory: Callable[[TauSDKSettings], ApplicationServices] = ApplicationServices.create,
) -> FastAPI:
    """Build the ASGI application without starting network dependencies."""
    resolved = settings or get_settings()
    configure_logging(
        resolved.log_level,
        machine_sink=resolved.log_machine_sink,
        human_sink=resolved.log_human_sink,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "runtime.started",
            message="Main Sequence TAU SDK process started",
            runtime_kind="coding_agent",
            runtime="tau",
            version=__version__,
        )
        services = services_factory(resolved)
        app.state.services = services
        app.state.settings = resolved
        app.state.backend = services.backend
        app.state.provider_factory = services.providers
        app.state.runtime_manager = services.runtime
        await services.start()
        logger.info(
            "runtime.ready",
            message="Main Sequence TAU SDK process is ready",
            runtime_kind="coding_agent",
            runtime="tau",
            version=__version__,
        )
        try:
            yield
        finally:
            logger.info(
                "runtime.shutdown.started",
                message="Main Sequence TAU SDK shutdown started",
                runtime_kind="coding_agent",
                runtime="tau",
            )
            await services.aclose()
            logger.info(
                "runtime.shutdown",
                message="Main Sequence TAU SDK stopped",
                runtime_kind="coding_agent",
                runtime="tau",
            )

    app = FastAPI(
        title="Main Sequence TAU SDK",
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

    @app.exception_handler(TauSDKError)
    async def sdk_error_handler(request: Request, error: TauSDKError) -> JSONResponse:
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
    app.include_router(responses.router)
    app.include_router(a2a.router)
    app.include_router(sessions.router)
    return app
