"""Async runtime-credential token exchange."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx
import structlog

from ms_tau_sdk.errors import BackendError, ConfigurationError
from ms_tau_sdk.settings import TauSDKSettings

from .routes import RUNTIME_CREDENTIAL_TOKEN

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class AccessToken:
    value: str
    token_type: str
    expires_at: float | None

    def needs_refresh(self, *, skew_seconds: int = 30) -> bool:
        return self.expires_at is not None and self.expires_at <= time.time() + skew_seconds


class RuntimeCredentialAuth:
    def __init__(
        self,
        settings: TauSDKSettings,
        *,
        exchange_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = exchange_client
        self._token: AccessToken | None = None
        self._lock = asyncio.Lock()

    def bind_client(self, client: httpx.AsyncClient) -> None:
        """Use the lifecycle-managed backend pool for token exchange."""
        if self._client is None:
            self._client = client

    async def headers(self, *, force: bool = False) -> dict[str, str]:
        token = await self._access_token(force=force)
        return {"Authorization": f"{token.token_type} {token.value}"}

    async def prefetch(self) -> None:
        """Authenticate during process startup so readiness is truthful."""
        await self._access_token(force=False)

    async def _access_token(self, *, force: bool) -> AccessToken:
        if not force and self._token is not None and not self._token.needs_refresh():
            return self._token
        async with self._lock:
            if not force and self._token is not None and not self._token.needs_refresh():
                return self._token
            self.settings.validate_runtime_auth()
            if (
                not self.settings.runtime_credential_id
                or not self.settings.runtime_credential_secret
            ):
                raise ConfigurationError("Runtime credentials are not configured")
            client = self._client or httpx.AsyncClient(timeout=10)
            close_client = self._client is None
            started_at = time.monotonic()
            try:
                response = await client.post(
                    f"{self.settings.backend_url.rstrip('/')}{RUNTIME_CREDENTIAL_TOKEN}",
                    json={
                        "credential_id": self.settings.runtime_credential_id,
                        "credential_secret": self.settings.runtime_credential_secret,
                    },
                )
            finally:
                if close_client:
                    await client.aclose()
            if not response.is_success:
                raise BackendError(
                    "Runtime credential exchange failed",
                    status_code=response.status_code,
                )
            data = response.json()
            value = str(data.get("access") or "").strip()
            if not value:
                raise BackendError("Runtime credential exchange returned no access token")
            expires_in = data.get("expires_in")
            expires_at = (
                time.time() + int(expires_in)
                if isinstance(expires_in, (int, float)) and expires_in > 0
                else None
            )
            self._token = AccessToken(
                value=value,
                token_type=str(data.get("token_type") or "Bearer"),
                expires_at=expires_at,
            )
            logger.info(
                "runtime.auth.exchange.completed",
                duration_ms=round((time.monotonic() - started_at) * 1000, 3),
                outcome="success",
            )
            return self._token
