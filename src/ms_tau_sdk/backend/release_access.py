"""Token access to one application release, from the platform's runtime access answer (ADR 0021)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx

from ms_tau_sdk.errors import ApplicationWakingError, BackendError

from .auth import AccessToken, _jwt_expiry
from .models import ReleaseRuntimeAccess
from .release_readiness import expected_cold_start_seconds, release_warmth


@dataclass(slots=True)
class ReleaseAccess:
    """A release's RPC URL and a token to call it. The token never appears in repr()."""

    rpc_url: httpx.URL
    token: AccessToken


def release_access_from_answer(answer: ReleaseRuntimeAccess) -> ReleaseAccess:
    """Read token access from the platform's answer to ``resolve-runtime-access``.

    A release that is still starting raises ``ApplicationWakingError`` with the wait the platform
    asks for. An answer without usable token access raises ``BackendError`` that never holds the
    token.
    """

    grant = answer.access
    if grant is None:
        state = answer.runtime_access.get("state")
        if state == "waking":
            retry_ms = answer.runtime_access.get("retry_after_ms")
            delay = 2.0
            if isinstance(retry_ms, int | float) and not isinstance(retry_ms, bool):
                try:
                    seconds = float(retry_ms) / 1000
                except OverflowError:
                    seconds = float("inf")
                if math.isfinite(seconds) and seconds >= 0:
                    delay = max(seconds, 0.1)
            release_warmth.forget(answer.resource_release_uid)
            raise ApplicationWakingError(
                retry_after_seconds=delay,
                expected_cold_start_seconds=expected_cold_start_seconds(answer.scaling),
            )
        raise BackendError(
            f"Release {answer.resource_release_uid} is not ready to be called"
            + (f" (runtime access: {state})" if isinstance(state, str) else ""),
            detail={"runtime_access_state": state if isinstance(state, str) else None},
        )
    token = grant.token.get_secret_value() if grant.token is not None else ""
    if grant.mode != "token" or not token or not grant.rpc_url:
        raise BackendError(
            f"Release {answer.resource_release_uid} offers no token access to call it"
        )
    try:
        rpc_url: httpx.URL | None = httpx.URL(grant.rpc_url)
    except httpx.InvalidURL:
        rpc_url = None
    if (
        rpc_url is None
        or rpc_url.scheme not in {"https", "http"}
        or not rpc_url.host
        or rpc_url.userinfo
    ):
        raise BackendError("Backend release access names an invalid RPC URL")
    expires_at = (
        grant.expires_at.timestamp() if grant.expires_at is not None else _jwt_expiry(token)
    )
    # A ready answer is a sign that the release serves now.
    release_warmth.served(answer.resource_release_uid, answer.scaling)
    return ReleaseAccess(
        rpc_url=rpc_url,
        token=AccessToken(
            value=token,
            token_type="Bearer",
            expires_at=float(expires_at) if expires_at is not None else None,
        ),
    )
