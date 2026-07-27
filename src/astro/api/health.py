"""Health and readiness routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from astro import __version__
from astro.runtime.manager import SessionRuntimeManager

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
    manager: RuntimeManagerDep,
) -> dict[str, object]:
    return {
        "ok": True,
        "runtime": "tau",
        **manager.snapshot(),
    }


@router.get("/version")
async def version() -> dict[str, str]:
    return {
        "runtime": "tau",
        "version": __version__,
    }
