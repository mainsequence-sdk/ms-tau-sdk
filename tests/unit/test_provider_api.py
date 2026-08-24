from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI

import astro.api.providers as providers_api
from astro.api.dependencies import backend, provider_signin_manager
from astro.backend.models import ProviderStatus


def _attempt(status: str = "awaiting_browser") -> dict[str, object]:
    return {
        "id": "attempt-1",
        "provider": "openai-codex",
        "status": status,
        "nextAction": {"type": "open_url", "url": "https://provider.example/auth"},
        "authUrl": "https://provider.example/auth",
        "authInstructions": "Complete sign-in.",
        "authKind": "oauth",
        "createdAt": "2026-08-21T10:00:00Z",
        "updatedAt": "2026-08-21T10:00:00Z",
        "completedAt": None,
        "error": None,
    }


async def test_interactive_provider_attempt_routes_are_session_bound(
    astro_app: FastAPI,
    asgi_client,
):
    signin = Mock()
    signin.start = AsyncMock(return_value=_attempt())
    signin.get.return_value = _attempt()
    signin.continue_attempt = AsyncMock(return_value=_attempt("running"))
    signin.cancel = AsyncMock(return_value=_attempt("cancelled"))
    astro_app.dependency_overrides[provider_signin_manager] = lambda: signin

    async with asgi_client(astro_app, lifespan=True) as client:
        started = await client.post(
            "/api/model-providers/openai-codex/signin",
            json={"agent_session_uid": "session-1"},
        )
        fetched = await client.get(
            "/api/model-providers/openai-codex/signin/attempt-1",
            params={"agent_session_uid": "session-1"},
        )
        continued = await client.post(
            "/api/model-providers/openai-codex/signin/attempt-1/manual",
            params={"agent_session_uid": "session-1"},
            json={"input": "short-lived-callback"},
        )
        cancelled = await client.post(
            "/api/model-providers/openai-codex/signin/attempt-1/cancel",
            params={"agent_session_uid": "session-1"},
        )

    assert started.status_code == 202
    assert started.json()["attempt"]["id"] == "attempt-1"
    assert fetched.status_code == 200
    assert continued.status_code == 202
    assert cancelled.status_code == 200
    signin.start.assert_awaited_once_with(
        provider="openai-codex",
        agent_session_uid="session-1",
    )
    signin.get.assert_called_once_with(
        provider="openai-codex",
        agent_session_uid="session-1",
        attempt_id="attempt-1",
    )
    signin.continue_attempt.assert_awaited_once_with(
        provider="openai-codex",
        agent_session_uid="session-1",
        attempt_id="attempt-1",
        value="short-lived-callback",
    )
    signin.cancel.assert_awaited_once_with(
        provider="openai-codex",
        agent_session_uid="session-1",
        attempt_id="attempt-1",
    )


async def test_complete_credential_signin_remains_backward_compatible(
    astro_app: FastAPI,
    asgi_client,
):
    backend_client = AsyncMock()
    backend_client.flush_provider_credential.return_value = {
        "status": "active",
        "version": 2,
    }
    astro_app.dependency_overrides[backend] = lambda: backend_client

    async with asgi_client(astro_app, lifespan=True) as client:
        response = await client.post(
            "/api/model-providers/openai-codex/signin",
            json={
                "agent_session_uid": "session-1",
                "credential": {
                    "type": "oauth",
                    "access": "secret",
                    "refresh": "secret",
                    "expires": 1_900_000_000_000,
                },
                "base_version": 1,
            },
        )

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    backend_client.flush_provider_credential.assert_awaited_once_with(
        provider="openai-codex",
        session_uid="session-1",
        credential={
            "type": "oauth",
            "access": "secret",
            "refresh": "secret",
            "expires": 1_900_000_000_000,
        },
        base_version=1,
    )


async def test_model_catalog_aliases_statuses_and_signoff_use_backend_contract(
    astro_app: FastAPI,
    asgi_client,
    monkeypatch,
):
    backend_client = AsyncMock()
    backend_client.list_provider_statuses.return_value = [
        ProviderStatus(
            provider="openai",
            status="active",
            credential_kind="api_key",
        )
    ]
    backend_client.revoke_provider_credential.return_value = {"version": 3}
    catalog = {
        "version": 2,
        "providers": [{"provider": "openai", "models": ["gpt-5.1"]}],
    }
    collect_catalog = AsyncMock(return_value=catalog)
    monkeypatch.setattr(providers_api, "collect_model_catalog", collect_catalog)
    astro_app.dependency_overrides[backend] = lambda: backend_client

    async with asgi_client(astro_app, lifespan=True) as client:
        chat_catalog = await client.get(
            "/api/chat/get_available_models",
            params={"agent_session_uid": "session-1"},
        )
        model_catalog = await client.get(
            "/api/models/catalog",
            params={"agent_session_uid": "session-1"},
        )
        statuses = await client.get(
            "/api/model-providers",
            params={"agent_session_uid": "session-1"},
        )
        signed_off = await client.post(
            "/api/model-providers/openai/signoff",
            json={"agent_session_uid": "session-1"},
        )

    assert chat_catalog.json() == model_catalog.json() == catalog
    assert statuses.json()["providers"] == [
        {
            "provider": "openai",
            "status": "active",
            "credential_kind": "api_key",
            "message": None,
        }
    ]
    assert signed_off.json() == {
        "ok": True,
        "provider": "openai",
        "authenticated": False,
        "version": 3,
    }
    assert collect_catalog.await_count == 2
    collect_catalog.assert_awaited_with(backend_client, session_uid="session-1")
    backend_client.list_provider_statuses.assert_awaited_once_with(session_uid="session-1")
    backend_client.revoke_provider_credential.assert_awaited_once_with(
        provider="openai",
        session_uid="session-1",
    )
