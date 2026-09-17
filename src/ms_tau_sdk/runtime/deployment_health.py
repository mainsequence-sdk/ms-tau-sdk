"""TAU readiness adapter for the platform-owned deployment health endpoint."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import cast

import structlog
from fastapi import FastAPI

from ms_tau_sdk.runtime.manager import SessionRuntimeManager

logger = structlog.get_logger(__name__)

RUNTIME_HEALTH_ABI_VERSION = "1"
DEPLOYMENT_READINESS_CHECK_STATE_ATTRIBUTE = "mainsequence_deployment_readiness_check"
TAU_READINESS_HOOK_STATE_ATTRIBUTE = "mainsequence_tau_readiness_hook"
READINESS_HOOK_TIMEOUT_SECONDS = 3.0

DeploymentReadinessHook = Callable[[], bool | Awaitable[bool]]


async def _invoke_readiness_hook(
    hook: DeploymentReadinessHook,
) -> object:
    if inspect.iscoroutinefunction(hook):
        async_hook = cast(Callable[[], Awaitable[bool]], hook)
        return await async_hook()
    sync_hook = cast(Callable[[], object], hook)
    result = await asyncio.to_thread(sync_hook)
    if inspect.isawaitable(result):
        return await cast(Awaitable[object], result)
    return result


def register_deployment_readiness_hook(
    app: FastAPI,
    hook: DeploymentReadinessHook,
) -> None:
    """Register one optional, side-effect-free project readiness predicate."""

    if not callable(hook):
        raise TypeError("The deployment readiness hook must be callable.")
    if getattr(app.state, TAU_READINESS_HOOK_STATE_ATTRIBUTE, None) is not None:
        raise RuntimeError("A deployment readiness hook is already registered.")
    setattr(app.state, TAU_READINESS_HOOK_STATE_ATTRIBUTE, hook)


async def evaluate_tau_deployment_readiness(app: FastAPI) -> bool:
    """Return the single TAU readiness predicate consumed by all aliases."""

    manager = getattr(app.state, "runtime_manager", None)
    if not isinstance(manager, SessionRuntimeManager):
        return False
    if not manager.deployment_ready:
        return False

    hook: DeploymentReadinessHook | None = getattr(
        app.state,
        TAU_READINESS_HOOK_STATE_ATTRIBUTE,
        None,
    )
    if hook is None:
        return True
    try:
        async with asyncio.timeout(READINESS_HOOK_TIMEOUT_SECONDS):
            result = await _invoke_readiness_hook(hook)
        return result is True
    except TimeoutError:
        logger.warning("deployment.readiness_hook.timeout")
    except Exception as exc:  # pragma: no cover - defensive redaction boundary
        logger.warning(
            "deployment.readiness_hook.failed",
            error_type=type(exc).__name__,
        )
    return False


def install_tau_deployment_readiness_adapter(app: FastAPI) -> None:
    """Publish the launcher-neutral TAU adapter on application state."""

    async def check() -> bool:
        return await evaluate_tau_deployment_readiness(app)

    setattr(app.state, DEPLOYMENT_READINESS_CHECK_STATE_ATTRIBUTE, check)
    app.state.mainsequence_runtime_health_abi_version = RUNTIME_HEALTH_ABI_VERSION
