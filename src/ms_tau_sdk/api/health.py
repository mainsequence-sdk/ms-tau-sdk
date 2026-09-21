"""Health and readiness routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ms_tau_sdk import __version__
from ms_tau_sdk.runtime.deployment_health import evaluate_tau_deployment_readiness
from ms_tau_sdk.runtime.manager import SessionRuntimeManager

from .dependencies import runtime_manager

router = APIRouter()
RuntimeManagerDep = Annotated[SessionRuntimeManager, Depends(runtime_manager)]


@router.get("/health")
async def health(
    manager: RuntimeManagerDep,
) -> dict[str, object]:
    return {
        "ok": True,
        "runtime": "tau",
        "version": __version__,
        **manager.snapshot(),
    }


@router.get("/ready")
async def ready(
    request: Request,
) -> JSONResponse:
    is_ready = await evaluate_tau_deployment_readiness(request.app)
    manager = getattr(request.app.state, "runtime_manager", None)
    snapshot = (
        manager.snapshot()
        if isinstance(manager, SessionRuntimeManager)
        else {"startup_ready": False, "draining": False}
    )
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={
            "ok": is_ready,
            "runtime": "tau",
            **snapshot,
        },
    )


@router.get("/version")
async def version() -> dict[str, str]:
    return {
        "runtime": "tau",
        "version": __version__,
    }
