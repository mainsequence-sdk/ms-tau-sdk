from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from astro.api.dependencies import backend, provider_signin_manager
from astro.app import create_app
from astro.settings import Settings


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


def test_interactive_provider_attempt_routes_are_session_bound(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            backend_url="http://backend:8000",
            runtime_credential_id="credential-id",
            runtime_credential_secret="credential-secret",
            project_root=tmp_path,
        )
    )
    signin = Mock()
    signin.start = AsyncMock(return_value=_attempt())
    signin.get.return_value = _attempt()
    signin.continue_attempt = AsyncMock(return_value=_attempt("running"))
    signin.cancel = AsyncMock(return_value=_attempt("cancelled"))
    app.dependency_overrides[provider_signin_manager] = lambda: signin

    with TestClient(app) as client:
        started = client.post(
            "/api/model-providers/openai-codex/signin",
            json={"agent_session_uid": "session-1"},
        )
        fetched = client.get(
            "/api/model-providers/openai-codex/signin/attempt-1",
            params={"agent_session_uid": "session-1"},
        )
        continued = client.post(
            "/api/model-providers/openai-codex/signin/attempt-1/manual",
            params={"agent_session_uid": "session-1"},
            json={"input": "short-lived-callback"},
        )
        cancelled = client.post(
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


def test_complete_credential_signin_remains_backward_compatible(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            backend_url="http://backend:8000",
            runtime_credential_id="credential-id",
            runtime_credential_secret="credential-secret",
            project_root=tmp_path,
        )
    )
    backend_client = AsyncMock()
    backend_client.flush_provider_credential.return_value = {
        "status": "active",
        "version": 2,
    }
    app.dependency_overrides[backend] = lambda: backend_client

    with TestClient(app) as client:
        response = client.post(
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
