"""Per-turn provenance stamps for Tau sessions (ADR-28, Tau amendments 1 and 2).

Every bounded ``user`` turn on a protected message route is stamped with a
tau-native custom entry so backend history can tell a human turn from an agent
turn, and which one, without a new chat role.

Identity comes from the request headers the coding-agent gateway sets from
Django's validation verdict (tdag-django ADR-0043). ``X-Caller-Kind`` is
mandatory: a protected route rejects the request before any session work when
it is absent or invalid. Nothing in a request body is trusted for identity.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

PROVENANCE_NAMESPACE = "io.mainsequence.provenance"

type TurnChannel = Literal["chat", "a2a"]
type TurnOrigin = Literal["user", "agent"]
type TurnProvenance = dict[str, str]
type CallerKind = Literal["user", "agent"]

CALLER_KIND_HEADER = "X-Caller-Kind"
CALLER_AGENT_UID_HEADER = "X-Caller-Agent-UID"
CALLER_SERVICE_UID_HEADER = "X-Caller-Coding-Agent-Service-UID"
CALLER_AGENT_SESSION_UID_HEADER = "X-Caller-Agent-Session-UID"
USER_UID_HEADER = "X-User-UID"
USERNAME_HEADER = "X-Username"

CALLER_IDENTITY_REJECTION_STATUS = 403
CALLER_IDENTITY_REJECTION_DETAIL = "Missing or invalid caller identity headers."
CALLER_IDENTITY_REJECTION_CODE = "runtime_caller_identity_invalid"

MAX_USERNAME_LENGTH = 255

_AGENT_ONLY_HEADERS = (
    CALLER_AGENT_UID_HEADER,
    CALLER_SERVICE_UID_HEADER,
    CALLER_AGENT_SESSION_UID_HEADER,
)

_ORIGIN_BY_CHANNEL: dict[str, TurnOrigin] = {
    "chat": "user",
    "a2a": "agent",
}


class CallerIdentityError(Exception):
    """A protected route received no valid gateway-verified caller identity."""

    def __init__(self, failing_headers: tuple[str, ...]) -> None:
        self.failing_headers = failing_headers
        self.status_code = CALLER_IDENTITY_REJECTION_STATUS
        self.detail = CALLER_IDENTITY_REJECTION_DETAIL
        self.code = CALLER_IDENTITY_REJECTION_CODE
        super().__init__(f"{self.detail} ({', '.join(failing_headers)})")

    def body(self) -> dict[str, str]:
        """The exact response body a protected route returns."""

        return {"detail": self.detail, "code": self.code}


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """Gateway-verified identity of the principal behind one request."""

    kind: CallerKind
    actor_uid: str
    actor_name: str | None = None
    caller_agent_uid: str | None = None
    caller_service_uid: str | None = None
    caller_agent_session_uid: str | None = None


def _header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    return str(value or "").strip()


def _is_canonical_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, TypeError, ValueError):
        return False


def validate_caller_identity(headers: Mapping[str, str]) -> CallerIdentity:
    """Return the caller identity carried by gateway headers, or raise.

    Rules (ADR-28 amendment 2):

    - ``X-Caller-Kind`` must be present and exactly ``user`` or ``agent``.
    - ``user``: ``X-User-UID`` must be a canonical lowercase UUID and the
      Agent-specific caller headers must be absent or empty.
    - ``agent``: ``X-Caller-Agent-UID`` and ``X-Caller-Coding-Agent-Service-UID``
      must be canonical lowercase UUIDs; ``X-Caller-Agent-Session-UID`` must be
      canonical when present.
    - ``X-Username``, when present, is bounded to 255 characters.
    """

    failing: list[str] = []
    kind = _header(headers, CALLER_KIND_HEADER)
    if kind not in ("user", "agent"):
        raise CallerIdentityError((CALLER_KIND_HEADER,))

    user_uid = _header(headers, USER_UID_HEADER)
    username = _header(headers, USERNAME_HEADER) or None
    if username is not None and len(username) > MAX_USERNAME_LENGTH:
        failing.append(USERNAME_HEADER)

    agent_uid = _header(headers, CALLER_AGENT_UID_HEADER)
    service_uid = _header(headers, CALLER_SERVICE_UID_HEADER)
    session_uid = _header(headers, CALLER_AGENT_SESSION_UID_HEADER)

    if kind == "user":
        if not _is_canonical_uuid(user_uid):
            failing.append(USER_UID_HEADER)
        for name, value in zip(
            _AGENT_ONLY_HEADERS, (agent_uid, service_uid, session_uid), strict=True
        ):
            if value:
                failing.append(name)
        if failing:
            raise CallerIdentityError(tuple(failing))
        return CallerIdentity(kind="user", actor_uid=user_uid, actor_name=username)

    if not _is_canonical_uuid(agent_uid):
        failing.append(CALLER_AGENT_UID_HEADER)
    if not _is_canonical_uuid(service_uid):
        failing.append(CALLER_SERVICE_UID_HEADER)
    if session_uid and not _is_canonical_uuid(session_uid):
        failing.append(CALLER_AGENT_SESSION_UID_HEADER)
    if failing:
        raise CallerIdentityError(tuple(failing))
    return CallerIdentity(
        kind="agent",
        actor_uid=agent_uid,
        caller_agent_uid=agent_uid,
        caller_service_uid=service_uid,
        caller_agent_session_uid=session_uid or None,
    )


def build_turn_provenance(channel: TurnChannel) -> TurnProvenance:
    """Return the route-only stamp for ``channel``.

    Kept for the route-to-channel mapping and for paths that carry no
    gateway identity; protected message routes use
    :func:`turn_provenance_from_request` instead.
    """

    try:
        origin = _ORIGIN_BY_CHANNEL[channel]
    except KeyError as exc:
        raise ValueError(f"Unknown turn channel: {channel!r}") from exc
    return {"channel": channel, "origin": origin}


def turn_provenance_from_request(
    channel: TurnChannel,
    headers: Mapping[str, str],
) -> TurnProvenance:
    """Build the stamp for a protected route from gateway-verified headers.

    ``origin`` follows ``X-Caller-Kind`` only; the route never decides it.
    Raises :class:`CallerIdentityError` when the identity is missing or invalid,
    so callers reject the request before any session work.
    """

    if channel not in _ORIGIN_BY_CHANNEL:
        raise ValueError(f"Unknown turn channel: {channel!r}")
    identity = validate_caller_identity(headers)
    stamp: TurnProvenance = {
        "channel": channel,
        "origin": identity.kind,
        "actorKind": identity.kind,
        "actorUid": identity.actor_uid,
    }
    if identity.kind == "user" and identity.actor_name:
        stamp["actorName"] = identity.actor_name
    if identity.caller_agent_session_uid:
        stamp["callerAgentSessionUid"] = identity.caller_agent_session_uid
    return stamp
