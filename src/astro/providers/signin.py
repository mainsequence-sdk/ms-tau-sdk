"""Tau-owned interactive model-provider sign-in attempts."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

import structlog
from tau_coding.oauth_registry import get_oauth_provider
from tau_coding.oauth_types import (
    OAuthAuthInfo,
    OAuthDeviceCodeInfo,
    OAuthLoginCallbacks,
    OAuthPrompt,
    OAuthProvider,
    OAuthSelectPrompt,
)

from astro.backend.client import MainSequenceClient
from astro.errors import AstroError

logger = structlog.get_logger(__name__)

AttemptStatus = Literal[
    "pending",
    "awaiting_browser",
    "awaiting_manual_input",
    "running",
    "completed",
    "failed",
    "cancelled",
]


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ProviderSignInError(AstroError):
    """Safe provider-attempt error with the established runtime shape."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int,
        attempt: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        detail = {"attempt": attempt} if attempt is not None else None
        super().__init__(message, detail=detail)


@dataclass(slots=True)
class ProviderSignInAttempt:
    """In-memory state owned by one maintained Tau runtime process."""

    id: str
    provider: str
    agent_session_uid: str
    base_version: int
    status: AttemptStatus = "pending"
    next_action: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "wait",
            "message": "Preparing provider sign-in.",
        }
    )
    authorization_url: str | None = None
    instructions: str | None = None
    created_at: str = field(default_factory=_timestamp)
    updated_at: str = field(default_factory=_timestamp)
    completed_at: str | None = None
    error: str | None = None
    first_update: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    input_future: asyncio.Future[str] | None = field(default=None, repr=False)

    def project(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "status": self.status,
            "nextAction": dict(self.next_action),
            "authUrl": self.authorization_url,
            "authInstructions": self.instructions,
            "authKind": "oauth",
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "completedAt": self.completed_at,
            "error": self.error,
        }


class ProviderSignInManager:
    """Coordinate provider OAuth without exposing credentials to API callers."""

    def __init__(self, backend: MainSequenceClient) -> None:
        self._backend = backend
        self._attempts: dict[str, ProviderSignInAttempt] = {}
        self._active_by_scope: dict[tuple[str, str], str] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        provider: str,
        agent_session_uid: str,
    ) -> dict[str, Any]:
        oauth_provider = get_oauth_provider(provider)
        if oauth_provider is None:
            raise ProviderSignInError(
                f'Provider "{provider}" does not expose interactive Tau sign-in.',
                code="provider_signin_not_available",
                status_code=409,
            )
        base_version = await self._credential_base_version(
            provider=provider,
            agent_session_uid=agent_session_uid,
        )
        scope = (agent_session_uid, provider)
        async with self._lock:
            existing_id = self._active_by_scope.get(scope)
            if existing_id is not None:
                existing = self._attempts[existing_id]
                raise ProviderSignInError(
                    f"A sign-in attempt for {provider} is already in progress.",
                    code="provider_signin_in_progress",
                    status_code=409,
                    attempt=existing.project(),
                )
            attempt = ProviderSignInAttempt(
                id=str(uuid4()),
                provider=provider,
                agent_session_uid=agent_session_uid,
                base_version=base_version,
            )
            self._attempts[attempt.id] = attempt
            self._active_by_scope[scope] = attempt.id
            task = asyncio.create_task(
                self._run(attempt, oauth_provider),
                name=f"provider-signin-{provider}-{attempt.id}",
            )
            self._tasks[attempt.id] = task
        with suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.shield(attempt.first_update.wait()),
                timeout=2.0,
            )
        return attempt.project()

    def get(
        self,
        *,
        provider: str,
        agent_session_uid: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        return self._resolve(
            provider=provider,
            agent_session_uid=agent_session_uid,
            attempt_id=attempt_id,
        ).project()

    async def continue_attempt(
        self,
        *,
        provider: str,
        agent_session_uid: str,
        attempt_id: str,
        value: str,
    ) -> dict[str, Any]:
        attempt = self._resolve(
            provider=provider,
            agent_session_uid=agent_session_uid,
            attempt_id=attempt_id,
        )
        if attempt.status != "awaiting_manual_input" or attempt.input_future is None:
            raise ProviderSignInError(
                "The provider sign-in attempt is not requesting input.",
                code="signin_manual_input_not_requested",
                status_code=409,
                attempt=attempt.project(),
            )
        allow_empty = attempt.next_action.get("allowEmpty") is True
        if not value.strip() and not allow_empty:
            raise ProviderSignInError(
                "Provider sign-in input must not be empty.",
                code="signin_manual_input_missing",
                status_code=400,
                attempt=attempt.project(),
            )
        input_future = attempt.input_future
        attempt.input_future = None
        self._update(
            attempt,
            status="running",
            next_action={
                "type": "wait",
                "message": "Completing provider sign-in.",
            },
        )
        if not input_future.done():
            input_future.set_result(value)
        await asyncio.sleep(0)
        return attempt.project()

    async def cancel(
        self,
        *,
        provider: str,
        agent_session_uid: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        attempt = self._resolve(
            provider=provider,
            agent_session_uid=agent_session_uid,
            attempt_id=attempt_id,
        )
        if attempt.status in {"completed", "failed", "cancelled"}:
            raise ProviderSignInError(
                "The provider sign-in attempt is no longer active.",
                code="signin_attempt_not_active",
                status_code=409,
                attempt=attempt.project(),
            )
        self._update(
            attempt,
            status="cancelled",
            next_action={"type": "none"},
            error="Provider sign-in was cancelled.",
            terminal=True,
        )
        task = self._tasks.get(attempt.id)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        return attempt.project()

    async def aclose(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _credential_base_version(
        self,
        *,
        provider: str,
        agent_session_uid: str,
    ) -> int:
        statuses = await self._backend.list_provider_statuses(
            session_uid=agent_session_uid,
        )
        for status in statuses:
            if status.provider != provider:
                continue
            raw_version = (status.model_extra or {}).get("version", 0)
            if isinstance(raw_version, int) and not isinstance(raw_version, bool):
                return max(raw_version, 0)
        return 0

    async def _run(
        self,
        attempt: ProviderSignInAttempt,
        provider: OAuthProvider,
    ) -> None:
        try:
            credential = await provider.login(self._callbacks(attempt))
            if attempt.status == "cancelled":
                return
            self._update(
                attempt,
                status="running",
                next_action={
                    "type": "wait",
                    "message": "Saving the provider credential.",
                },
            )
            await self._backend.flush_provider_credential(
                provider=attempt.provider,
                session_uid=attempt.agent_session_uid,
                credential=credential.to_json(),
                base_version=attempt.base_version,
            )
            self._update(
                attempt,
                status="completed",
                next_action={"type": "none"},
                terminal=True,
            )
        except asyncio.CancelledError:
            if attempt.status != "cancelled":
                self._update(
                    attempt,
                    status="cancelled",
                    next_action={"type": "none"},
                    error="Provider sign-in was cancelled.",
                    terminal=True,
                )
            raise
        except Exception as exc:
            logger.warning(
                "astro.provider_signin.failed",
                provider=attempt.provider,
                attempt_id=attempt.id,
                error_type=type(exc).__name__,
            )
            self._update(
                attempt,
                status="failed",
                next_action={"type": "none"},
                error="Provider sign-in failed.",
                terminal=True,
            )
        finally:
            self._active_by_scope.pop(
                (attempt.agent_session_uid, attempt.provider),
                None,
            )
            self._tasks.pop(attempt.id, None)

    def _callbacks(self, attempt: ProviderSignInAttempt) -> OAuthLoginCallbacks:
        return OAuthLoginCallbacks(
            on_auth=lambda info: self._on_auth(attempt, info),
            on_device_code=lambda info: self._on_device_code(attempt, info),
            on_prompt=lambda prompt: self._request_input(attempt, prompt),
            on_select=lambda prompt: self._request_selection(attempt, prompt),
            on_progress=lambda _message: self._update(
                attempt,
                status="running",
                next_action={
                    "type": "wait",
                    "message": "Waiting for the provider to finish authentication.",
                },
            ),
            on_manual_code_input=lambda: self._request_callback_input(attempt),
        )

    def _on_auth(
        self,
        attempt: ProviderSignInAttempt,
        info: OAuthAuthInfo,
    ) -> None:
        self._update(
            attempt,
            status="awaiting_browser",
            next_action={
                "type": "open_url",
                "url": info.url,
                **({"instructions": info.instructions} if info.instructions else {}),
            },
            authorization_url=info.url,
            instructions=info.instructions,
        )

    def _on_device_code(
        self,
        attempt: ProviderSignInAttempt,
        info: OAuthDeviceCodeInfo,
    ) -> None:
        instructions = f"Open the provider page and enter code {info.user_code}."
        self._update(
            attempt,
            status="awaiting_browser",
            next_action={
                "type": "open_url",
                "url": info.verification_uri,
                "instructions": instructions,
            },
            authorization_url=info.verification_uri,
            instructions=instructions,
        )

    async def _request_input(
        self,
        attempt: ProviderSignInAttempt,
        prompt: OAuthPrompt,
    ) -> str:
        return await self._wait_for_input(
            attempt,
            next_action={
                "type": "prompt_input",
                "prompt": prompt.message,
                **({"placeholder": prompt.placeholder} if prompt.placeholder else {}),
                "allowEmpty": prompt.allow_empty,
            },
        )

    async def _request_selection(
        self,
        attempt: ProviderSignInAttempt,
        prompt: OAuthSelectPrompt,
    ) -> str | None:
        choices = ", ".join(f"{option.id} ({option.label})" for option in prompt.options)
        return await self._wait_for_input(
            attempt,
            next_action={
                "type": "prompt_input",
                "prompt": f"{prompt.message} Choices: {choices}",
                "allowEmpty": True,
            },
        )

    async def _request_callback_input(
        self,
        attempt: ProviderSignInAttempt,
    ) -> str:
        return await self._wait_for_input(
            attempt,
            next_action={
                "type": "enter_callback_url",
                "prompt": "Paste the authorization code or full redirect URL.",
                "instructions": (
                    "After provider sign-in, copy the final redirect URL from "
                    "the browser address bar and submit it to this attempt."
                ),
            },
        )

    async def _wait_for_input(
        self,
        attempt: ProviderSignInAttempt,
        *,
        next_action: dict[str, Any],
    ) -> str:
        if attempt.input_future is not None:
            raise RuntimeError("Provider sign-in already has a pending input request")
        attempt.input_future = asyncio.get_running_loop().create_future()
        self._update(
            attempt,
            status="awaiting_manual_input",
            next_action=next_action,
        )
        return await attempt.input_future

    def _resolve(
        self,
        *,
        provider: str,
        agent_session_uid: str,
        attempt_id: str,
    ) -> ProviderSignInAttempt:
        attempt = self._attempts.get(attempt_id)
        if (
            attempt is None
            or attempt.provider != provider
            or attempt.agent_session_uid != agent_session_uid
        ):
            raise ProviderSignInError(
                "The provider sign-in attempt was not found.",
                code="signin_attempt_not_found",
                status_code=404,
            )
        return attempt

    @staticmethod
    def _update(
        attempt: ProviderSignInAttempt,
        *,
        status: AttemptStatus,
        next_action: dict[str, Any],
        authorization_url: str | None = None,
        instructions: str | None = None,
        error: str | None = None,
        terminal: bool = False,
    ) -> None:
        attempt.status = status
        attempt.next_action = next_action
        if authorization_url is not None:
            attempt.authorization_url = authorization_url
        if instructions is not None:
            attempt.instructions = instructions
        attempt.error = error
        attempt.updated_at = _timestamp()
        if terminal:
            attempt.completed_at = attempt.updated_at
        attempt.first_update.set()
