from __future__ import annotations

from fastapi import FastAPI

import ms_tau_sdk
from ms_tau_sdk.runtime.deployment_health import (
    DEPLOYMENT_READINESS_CHECK_STATE_ATTRIBUTE,
)


async def test_ready_compatibility_alias_is_not_ready_before_lifespan_startup(
    sdk_app: FastAPI,
    asgi_client,
) -> None:
    async with asgi_client(sdk_app, lifespan=False) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert response.json()["startup_ready"] is False


async def test_tau_registers_launcher_readiness_adapter_automatically(
    sdk_app: FastAPI,
    asgi_client,
) -> None:
    check = getattr(
        sdk_app.state,
        DEPLOYMENT_READINESS_CHECK_STATE_ATTRIBUTE,
    )

    assert await check() is False
    async with asgi_client(sdk_app, lifespan=True) as client:
        assert await check() is True
        compatibility = await client.get("/ready")

    assert compatibility.status_code == 200
    assert compatibility.json()["ok"] is True
    assert ms_tau_sdk.RUNTIME_HEALTH_ABI_VERSION == "1"


async def test_optional_hook_controls_adapter_and_ready_compatibility_alias(
    sdk_app: FastAPI,
    asgi_client,
) -> None:
    hook_state = {"ready": False}

    async def hook() -> bool:
        return hook_state["ready"]

    ms_tau_sdk.register_deployment_readiness_hook(sdk_app, hook)
    check = getattr(
        sdk_app.state,
        DEPLOYMENT_READINESS_CHECK_STATE_ATTRIBUTE,
    )

    async with asgi_client(sdk_app, lifespan=True) as client:
        assert await check() is False
        pending = await client.get("/ready")
        hook_state["ready"] = True
        assert await check() is True
        ready = await client.get("/ready")

    assert pending.status_code == 503
    assert pending.json()["ok"] is False
    assert ready.status_code == 200
    assert ready.json()["ok"] is True


async def test_hook_failure_is_sanitized_and_duplicate_registration_fails(
    sdk_app: FastAPI,
    asgi_client,
) -> None:
    async def failing_hook() -> bool:
        raise RuntimeError("secret readiness detail")

    ms_tau_sdk.register_deployment_readiness_hook(sdk_app, failing_hook)
    try:
        ms_tau_sdk.register_deployment_readiness_hook(sdk_app, lambda: True)
    except RuntimeError as exc:
        assert "already registered" in str(exc)
    else:  # pragma: no cover - assertion aid
        raise AssertionError("Expected duplicate hook registration to fail")

    async with asgi_client(sdk_app, lifespan=True) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert "secret" not in response.text
