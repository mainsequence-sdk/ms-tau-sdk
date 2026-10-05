"""Verify the platform's signed assertions on requests to a hosted runtime.

The platform signs two kinds of short-lived JWS with one Ed25519 key set and sends both in the
``X-MainSequence-Caller-Assertion`` header:

- a caller assertion, for a request a person or a workload sends to the runtime through the
  platform. It names the caller (``sub``), the caller's teams in the Organization
  (``team_uids``), and whether the caller administers the Organization
  (``is_organization_admin``);
- a platform assertion, for the platform's own background calls. It names no caller.

Both are bound to this runtime's release (``aud``, ``resource_release_uid``) and Organization
Environment (``organization_environment_uid``) and live at most 300 seconds. The runtime takes its
release, Environment, issuer, and key-set URL from the settings the platform deploys it with, never
from the request.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ms_tau_sdk.errors import ConfigurationError
from ms_tau_sdk.settings import (
    CALLER_ASSERTION_ISSUER_ENV,
    CALLER_ASSERTION_JWKS_URL_ENV,
    ORGANIZATION_ENVIRONMENT_UID_ENV,
    RESOURCE_RELEASE_UID_ENV,
    TauSDKSettings,
)

ASSERTION_HEADER = "X-MainSequence-Caller-Assertion"
CALLER_ASSERTION_TYPE = "mainsequence-caller-assertion+jwt"
PLATFORM_ASSERTION_TYPE = "mainsequence-platform-assertion+jwt"
ASSERTION_ALGORITHM = "EdDSA"
AUDIENCE_PREFIX = "urn:mainsequence:fapi:"
MAX_ASSERTION_LIFETIME_SECONDS = 300
PLATFORM_ASSERTION_CLAIMS = frozenset(
    {
        "iss",
        "aud",
        "resource_release_uid",
        "organization_environment_uid",
        "iat",
        "nbf",
        "exp",
    }
)
CALLER_ASSERTION_CLAIMS = PLATFORM_ASSERTION_CLAIMS | {"sub", "team_uids", "is_organization_admin"}

type AssertionKind = Literal["caller", "platform"]

_ASSERTION_TYPES: dict[AssertionKind, str] = {
    "caller": CALLER_ASSERTION_TYPE,
    "platform": PLATFORM_ASSERTION_TYPE,
}
_ASSERTION_CLAIMS: dict[AssertionKind, frozenset[str]] = {
    "caller": CALLER_ASSERTION_CLAIMS,
    "platform": PLATFORM_ASSERTION_CLAIMS,
}
_MAX_AGE = re.compile(r"(?:^|,)\s*max-age\s*=\s*(\d+)\s*(?:,|$)", re.IGNORECASE)


class InvalidAssertionError(Exception):
    """The request carries no valid assertion of the kind its route requires."""


class AssertionKeysUnavailableError(Exception):
    """The platform's verification keys could not be obtained."""


@dataclass(frozen=True, slots=True)
class VerifiedCaller:
    """The caller that a verified caller assertion names."""

    uid: str
    team_uids: tuple[str, ...]
    is_organization_admin: bool


@dataclass(frozen=True, slots=True)
class VerifiedAssertion:
    """A verified assertion. A platform assertion names no caller."""

    kind: AssertionKind
    caller: VerifiedCaller | None
    issued_at: int
    expires_at: int


def _is_canonical_uid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _is_https_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
    )


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def jwk_thumbprint(x: str) -> str:
    """Return the RFC 7638 thumbprint of the Ed25519 public JWK whose key is ``x``."""

    members = json.dumps(
        {"crv": "Ed25519", "kty": "OKP", "x": x},
        separators=(",", ":"),
        sort_keys=True,
    )
    return _b64url_encode(hashlib.sha256(members.encode("ascii")).digest())


def _ed25519_key(entry: object) -> tuple[str, Ed25519PublicKey] | None:
    """Return the key ID and key of a usable Ed25519 signing JWK, or None for any other entry.

    A usable key's ``kid`` is its RFC 7638 thumbprint.
    """

    if not isinstance(entry, Mapping):
        return None
    if entry.get("kty") != "OKP" or entry.get("crv") != "Ed25519":
        return None
    if entry.get("alg", ASSERTION_ALGORITHM) != ASSERTION_ALGORITHM:
        return None
    if entry.get("use", "sig") != "sig":
        return None
    x = entry.get("x")
    kid = entry.get("kid")
    if not isinstance(x, str) or not isinstance(kid, str):
        return None
    try:
        raw = _b64url_decode(x)
        if len(raw) != 32 or _b64url_encode(raw) != x or kid != jwk_thumbprint(x):
            return None
        return kid, Ed25519PublicKey.from_public_bytes(raw)
    except ValueError:
        return None


def _key_set(document: object) -> dict[str, Ed25519PublicKey]:
    entries = document.get("keys") if isinstance(document, Mapping) else None
    keys: dict[str, Ed25519PublicKey] = {}
    for entry in entries if isinstance(entries, list) else ():
        parsed = _ed25519_key(entry)
        if parsed is not None:
            keys[parsed[0]] = parsed[1]
    return keys


def _fresh_until(cache_control: str | None, now: float) -> float | None:
    """Return when a fetched key set stops being fresh, or None to keep it until an unknown kid."""

    match = _MAX_AGE.search(cache_control or "")
    return now + int(match.group(1)) if match else None


def _verified_caller(payload: Mapping[str, Any]) -> VerifiedCaller:
    uid = payload["sub"]
    team_uids = payload["team_uids"]
    is_organization_admin = payload["is_organization_admin"]
    if (
        not _is_canonical_uid(uid)
        or not isinstance(team_uids, list)
        or not all(_is_canonical_uid(team_uid) for team_uid in team_uids)
        or team_uids != sorted(set(team_uids))
        or type(is_organization_admin) is not bool
    ):
        raise InvalidAssertionError("The caller assertion does not name a valid caller")
    return VerifiedCaller(
        uid=uid,
        team_uids=tuple(team_uids),
        is_organization_admin=is_organization_admin,
    )


class AssertionVerifier:
    """Verify assertions against this runtime's release, Environment, issuer, and key set.

    The key set is fetched from the platform when first needed and stays fresh for the
    ``max-age`` its response declares; without one, it is kept until a token names a key it does
    not hold. An unknown ``kid`` refreshes the set once. A key set that cannot be fetched is
    reported as unavailable; a stale one is never used in its place.
    """

    def __init__(
        self,
        *,
        issuer: str,
        jwks_url: str,
        resource_release_uid: str,
        organization_environment_uid: str,
        timeout: httpx.Timeout,
        max_response_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        problems: list[str] = []
        if not issuer.strip():
            problems.append(f"{CALLER_ASSERTION_ISSUER_ENV} is not set")
        if not _is_https_url(jwks_url):
            problems.append(f"{CALLER_ASSERTION_JWKS_URL_ENV} is not an HTTPS URL")
        if not _is_canonical_uid(resource_release_uid):
            problems.append(f"{RESOURCE_RELEASE_UID_ENV} is not a canonical lowercase UUID")
        if not _is_canonical_uid(organization_environment_uid):
            problems.append(f"{ORGANIZATION_ENVIRONMENT_UID_ENV} is not a canonical lowercase UUID")
        if problems:
            raise ConfigurationError(
                "Hosted caller authentication is not configured: " + "; ".join(problems)
            )
        self.issuer = issuer
        self.jwks_url = jwks_url
        self.resource_release_uid = resource_release_uid
        self.organization_environment_uid = organization_environment_uid
        self.audience = AUDIENCE_PREFIX + resource_release_uid
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        self._transport = transport
        self._clock = clock
        self._keys: dict[str, Ed25519PublicKey] = {}
        self._fresh_until: float | None = None
        self._generation = 0
        self._refresh_lock = asyncio.Lock()

    @classmethod
    def from_settings(
        cls,
        settings: TauSDKSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> AssertionVerifier:
        """Build the verifier from the settings the platform deploys the runtime with."""

        return cls(
            issuer=settings.caller_assertion_issuer or "",
            jwks_url=settings.caller_assertion_jwks_url or "",
            resource_release_uid=settings.resource_release_uid or "",
            organization_environment_uid=settings.organization_environment_uid or "",
            timeout=httpx.Timeout(
                connect=settings.backend_connect_timeout_seconds,
                read=settings.backend_read_timeout_seconds,
                write=settings.backend_write_timeout_seconds,
                pool=settings.backend_pool_timeout_seconds,
            ),
            max_response_bytes=settings.backend_max_response_bytes,
            transport=transport,
        )

    async def verify(self, token: str, *, kind: AssertionKind) -> VerifiedAssertion:
        """Return the verified assertion, or raise.

        Raises InvalidAssertionError for any token that is not a valid assertion of ``kind`` for
        this runtime, and AssertionKeysUnavailableError when the key set cannot be fetched.
        """

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise InvalidAssertionError("The assertion is not a JWS") from error
        kid = header.get("kid")
        if (
            set(header) != {"alg", "kid", "typ"}
            or header.get("alg") != ASSERTION_ALGORITHM
            or header.get("typ") != _ASSERTION_TYPES[kind]
            or not isinstance(kid, str)
            or not kid
        ):
            raise InvalidAssertionError(f"The token is not a {kind} assertion")
        key = await self._key(kid)
        claims = _ASSERTION_CLAIMS[kind]
        try:
            payload = jwt.decode(
                token,
                key=key,
                algorithms=[ASSERTION_ALGORITHM],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": sorted(claims), "strict_aud": True},
            )
        except (jwt.PyJWTError, TypeError, ValueError) as error:
            raise InvalidAssertionError(f"The {kind} assertion did not verify") from error
        if set(payload) != claims:
            raise InvalidAssertionError(f"The {kind} assertion does not have the exact claim set")
        if (
            payload["iss"] != self.issuer
            or payload["aud"] != self.audience
            or payload["resource_release_uid"] != self.resource_release_uid
            or payload["organization_environment_uid"] != self.organization_environment_uid
        ):
            raise InvalidAssertionError(
                f"The {kind} assertion is for another issuer, release, or Organization Environment"
            )
        issued_at, not_before, expires_at = payload["iat"], payload["nbf"], payload["exp"]
        if not all(type(value) is int for value in (issued_at, not_before, expires_at)) or not (
            not_before <= issued_at < expires_at <= issued_at + MAX_ASSERTION_LIFETIME_SECONDS
        ):
            raise InvalidAssertionError(f"The {kind} assertion lifetime is invalid")
        return VerifiedAssertion(
            kind=kind,
            caller=_verified_caller(payload) if kind == "caller" else None,
            issued_at=issued_at,
            expires_at=expires_at,
        )

    def _fresh(self) -> bool:
        return bool(self._keys) and (self._fresh_until is None or self._clock() < self._fresh_until)

    async def _key(self, kid: str) -> Ed25519PublicKey:
        generation = self._generation
        if self._fresh() and kid in self._keys:
            return self._keys[kid]
        async with self._refresh_lock:
            # Another verification may have fetched the key set while this one waited for it.
            if self._generation == generation or not self._fresh():
                await self._refresh()
            key = self._keys.get(kid)
        if key is None:
            raise InvalidAssertionError("The assertion names a key the platform does not publish")
        return key

    async def _refresh(self) -> None:
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    self.jwks_url,
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as error:
            raise AssertionKeysUnavailableError(
                "The platform's assertion keys could not be fetched"
            ) from error
        if response.status_code != 200:
            raise AssertionKeysUnavailableError(
                f"The platform's assertion key set answered HTTP {response.status_code}"
            )
        if len(response.content) > self._max_response_bytes:
            raise AssertionKeysUnavailableError("The platform's assertion key set is too large")
        try:
            document = response.json()
        except ValueError as error:
            raise AssertionKeysUnavailableError(
                "The platform's assertion key set is not JSON"
            ) from error
        keys = _key_set(document)
        if not keys:
            raise AssertionKeysUnavailableError(
                "The platform's assertion key set has no usable Ed25519 key"
            )
        self._keys = keys
        self._fresh_until = _fresh_until(response.headers.get("cache-control"), self._clock())
        self._generation += 1


__all__ = [
    "ASSERTION_HEADER",
    "CALLER_ASSERTION_TYPE",
    "PLATFORM_ASSERTION_TYPE",
    "AssertionKeysUnavailableError",
    "AssertionKind",
    "AssertionVerifier",
    "InvalidAssertionError",
    "VerifiedAssertion",
    "VerifiedCaller",
    "jwk_thumbprint",
]
