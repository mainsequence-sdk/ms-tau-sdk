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


def _retry_after_seconds(retry_ms: object) -> float | None:
    """The wait a valid ``retry_after_ms`` asks for, at least 100 ms; ``None`` without one."""

    if not isinstance(retry_ms, int | float) or isinstance(retry_ms, bool):
        return None
    try:
        seconds = float(retry_ms) / 1000
    except OverflowError:
        return None
    return max(seconds, 0.1) if math.isfinite(seconds) and seconds >= 0 else None


def release_access_from_answer(answer: ReleaseRuntimeAccess) -> ReleaseAccess:
    """Read token access from the platform's answer to ``resolve-runtime-access``.

    An answer without access that carries ``retry_after_ms`` raises ``ApplicationWakingError``
    with that wait, whatever its state; so does ``waking`` without one, after two seconds. Any
    other answer without usable token access raises ``BackendError`` that never holds the token.
    """

    grant = answer.access
    if grant is None:
        raw_state = answer.runtime_access.get("state")
        state = raw_state if isinstance(raw_state, str) else None
        delay = _retry_after_seconds(answer.runtime_access.get("retry_after_ms"))
        if delay is None and state == "waking":
            delay = 2.0
        if delay is not None:
            release_warmth.forget(answer.resource_release_uid)
            raise ApplicationWakingError(
                retry_after_seconds=delay,
                expected_cold_start_seconds=expected_cold_start_seconds(answer.scaling),
                runtime_access_state=state,
            )
        raise BackendError(
            f"Release {answer.resource_release_uid} is not ready to be called"
            + (f" (runtime access: {state})" if state is not None else ""),
            detail={"runtime_access_state": state},
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
