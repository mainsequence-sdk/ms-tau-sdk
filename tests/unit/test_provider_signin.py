from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from tau_coding.credentials import OAuthCredential
from tau_coding.oauth_types import (
    OAuthAuthInfo,
    OAuthLoginCallbacks,
    OAuthPrompt,
    OAuthRuntimeAuth,
)

from astro.backend.models import ProviderStatus
from astro.providers.signin import ProviderSignInError, ProviderSignInManager


class BrowserOAuthProvider:
    id = "openai-codex"
    name = "OpenAI Codex"
    flow_kinds = ("browser",)

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredential:
        callbacks.on_auth(
            OAuthAuthInfo(
                url="https://provider.example/authorize?state=temporary",
                instructions="Complete provider sign-in.",
            )
        )
        manual_input = await callbacks.on_manual_code_input()  # type: ignore[misc]
        assert manual_input == "https://localhost/callback?code=short-lived"
        return OAuthCredential(
            access="access-secret",
            refresh="refresh-secret",
            expires=1_900_000_000_000,
        )

    async def refresh(self, credential: OAuthCredential) -> OAuthCredential:
        return credential

    def runtime_auth(self, credential: OAuthCredential) -> OAuthRuntimeAuth:
        return OAuthRuntimeAuth(api_key=credential.access)


class OptionalPromptOAuthProvider(BrowserOAuthProvider):
    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredential:
        value = await callbacks.on_prompt(  # type: ignore[misc]
            OAuthPrompt(
                message="Enterprise domain (optional)",
                placeholder="company.ghe.com",
                allow_empty=True,
            )
        )
        assert value == ""
        return OAuthCredential(
            access="access-secret",
            refresh="refresh-secret",
            expires=1_900_000_000_000,
        )


def _backend() -> AsyncMock:
    backend = AsyncMock()
    backend.list_provider_statuses.return_value = [
        ProviderStatus(
            provider="openai-codex",
            status="active",
            credential_kind="oauth",
            version=8,
        )
    ]
    backend.flush_provider_credential.return_value = {
        "provider": "openai-codex",
        "status": "active",
        "version": 9,
    }
    return backend


@pytest.mark.asyncio
async def test_browser_attempt_continues_and_flushes_without_projecting_secrets():
    backend = _backend()
    manager = ProviderSignInManager(backend)
    with patch(
        "astro.providers.signin.get_oauth_provider",
        return_value=BrowserOAuthProvider(),
    ):
        started = await manager.start(
            provider="openai-codex",
            agent_session_uid="session-1",
        )

        assert started["status"] == "awaiting_manual_input"
        assert started["authUrl"].startswith("https://provider.example/")
        assert started["nextAction"]["type"] == "enter_callback_url"
        assert "access-secret" not in str(started)

        continued = await manager.continue_attempt(
            provider="openai-codex",
            agent_session_uid="session-1",
            attempt_id=started["id"],
            value="https://localhost/callback?code=short-lived",
        )
        assert continued["status"] in {"running", "completed"}

        for _ in range(20):
            completed = manager.get(
                provider="openai-codex",
                agent_session_uid="session-1",
                attempt_id=started["id"],
            )
            if completed["status"] == "completed":
                break
            await asyncio.sleep(0)

        assert completed["status"] == "completed"
        assert completed["nextAction"] == {"type": "none"}
        assert "access-secret" not in str(completed)
        backend.flush_provider_credential.assert_awaited_once_with(
            provider="openai-codex",
            session_uid="session-1",
            credential={
                "type": "oauth",
                "access": "access-secret",
                "refresh": "refresh-secret",
                "expires": 1_900_000_000_000,
            },
            base_version=8,
        )
    await manager.aclose()


@pytest.mark.asyncio
async def test_attempt_can_be_cancelled_and_is_bound_to_exact_session():
    manager = ProviderSignInManager(_backend())
    with patch(
        "astro.providers.signin.get_oauth_provider",
        return_value=BrowserOAuthProvider(),
    ):
        started = await manager.start(
            provider="openai-codex",
            agent_session_uid="session-1",
        )

        with pytest.raises(ProviderSignInError) as raised:
            manager.get(
                provider="openai-codex",
                agent_session_uid="another-session",
                attempt_id=started["id"],
            )
        assert raised.value.code == "signin_attempt_not_found"

        cancelled = await manager.cancel(
            provider="openai-codex",
            agent_session_uid="session-1",
            attempt_id=started["id"],
        )
        assert cancelled["status"] == "cancelled"
        assert cancelled["nextAction"] == {"type": "none"}
    await manager.aclose()


@pytest.mark.asyncio
async def test_provider_without_tau_oauth_flow_is_rejected():
    manager = ProviderSignInManager(_backend())
    with patch("astro.providers.signin.get_oauth_provider", return_value=None):
        with pytest.raises(ProviderSignInError) as raised:
            await manager.start(
                provider="openai",
                agent_session_uid="session-1",
            )
    assert raised.value.code == "provider_signin_not_available"
    await manager.aclose()


@pytest.mark.asyncio
async def test_optional_provider_prompt_accepts_an_empty_response():
    backend = _backend()
    manager = ProviderSignInManager(backend)
    with patch(
        "astro.providers.signin.get_oauth_provider",
        return_value=OptionalPromptOAuthProvider(),
    ):
        started = await manager.start(
            provider="openai-codex",
            agent_session_uid="session-1",
        )

        assert started["status"] == "awaiting_manual_input"
        assert started["nextAction"]["type"] == "prompt_input"
        assert started["nextAction"]["allowEmpty"] is True

        continued = await manager.continue_attempt(
            provider="openai-codex",
            agent_session_uid="session-1",
            attempt_id=started["id"],
            value="",
        )
        assert continued["status"] in {"running", "completed"}

        for _ in range(20):
            completed = manager.get(
                provider="openai-codex",
                agent_session_uid="session-1",
                attempt_id=started["id"],
            )
            if completed["status"] == "completed":
                break
            await asyncio.sleep(0)

        assert completed["status"] == "completed"
        backend.flush_provider_credential.assert_awaited_once()
    await manager.aclose()
