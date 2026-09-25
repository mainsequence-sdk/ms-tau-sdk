"""FastAPI application and Uvicorn entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ms_tau_sdk import __version__
from ms_tau_sdk.api import a2a, chat, health, inspection, sessions
from ms_tau_sdk.application import ApplicationServices
from ms_tau_sdk.errors import TauSDKError
from ms_tau_sdk.logging import RequestContextMiddleware, configure_logging
from ms_tau_sdk.runtime.deployment_health import (
    install_tau_deployment_readiness_adapter,
)
from ms_tau_sdk.settings import TauSDKSettings, get_settings

logger = structlog.get_logger(__name__)
LOCAL_UNSUPPORTED_PATH_PREFIXES = ("/internal/a2a",)


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
        file_path=resolved.local_log_path if resolved.local_mode else None,
    )
    if resolved.local_mode and not resolved.loopback_bind:
        logger.warning(
            "local_mode.external_bind",
            message=(
                "Local mode is bound beyond loopback; accepted requests act with the "
                "authenticated Main Sequence user's live platform authority"
            ),
            host=resolved.host,
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
    install_tau_deployment_readiness_adapter(app)
    if resolved.trusted_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.trusted_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(RequestContextMiddleware)

    @app.middleware("http")
    async def local_mode_capability_boundary(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if resolved.local_mode and request.url.path.startswith(LOCAL_UNSUPPORTED_PATH_PREFIXES):
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "error": "local_mode_capability_unsupported",
                    "message": (
                        "This route requires a registered Main Sequence Agent or AgentSession"
                    ),
                    "detail": {"mode": "local", "path": request.url.path},
                },
            )
        return await call_next(request)

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
    app.include_router(a2a.router)
    app.include_router(sessions.router)
    app.include_router(inspection.router)
    return app
