"""Team membership checks a hosted runtime makes on demand (ADR-0019, #103).

The platform answers which of the given teams a person is an active member of. The runtime asks
only about the verified caller of the current request, with that request's caller assertion. It
keeps the answers for the rest of the request and, for as long as the application chose, for
later requests in the process.
"""

from __future__ import annotations

import math
import threading
import time
import uuid
import warnings
from collections import OrderedDict
from collections.abc import Iterable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ms_tau_sdk.errors import TauSDKError

if TYPE_CHECKING:
    from ms_tau_sdk.backend.assertions import CallerAssertion
    from ms_tau_sdk.backend.client import MainSequenceClient

# At most this many answers per process, and at most this many teams in one call to the platform,
# which answers no more in one call.
MAX_CACHED_ANSWERS = 10_000
MAX_TEAMS_PER_CALL = 100

# The release UID, Organization Environment UID, person UID and team UID.
type AnswerKey = tuple[str, str, str, str]


def _now() -> float:
    return time.monotonic()


class TeamMembershipCache:
    """Yes-or-no answers across requests, oldest dropped first.

    The key starts with the release and Organization Environment the runtime is deployed for, so
    an answer obtained for one never answers another.
    """

    def __init__(self, max_answers: int = MAX_CACHED_ANSWERS) -> None:
        self._answers: OrderedDict[AnswerKey, tuple[bool, float]] = OrderedDict()
        self._max_answers = max_answers
        self._lock = threading.Lock()

    def get(self, key: AnswerKey, *, max_age: float) -> bool | None:
        """Return the stored answer when it is younger than ``max_age`` seconds, else None."""

        if max_age <= 0:
            return None
        with self._lock:
            entry = self._answers.get(key)
        if entry is None:
            return None
        answer, stored_at = entry
        return answer if _now() - stored_at < max_age else None

    def put(self, key: AnswerKey, answer: bool) -> None:
        with self._lock:
            self._answers[key] = (answer, _now())
            self._answers.move_to_end(key)
            while len(self._answers) > self._max_answers:
                self._answers.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._answers)


@dataclass(slots=True)
class TeamMembershipScope:
    """What one hosted request may use to answer ``in_teams()``.

    The release and Organization Environment are the ones the runtime's verifier reads from its
    deployment configuration, never from the request, and every answer's key starts with them.
    The request's caller assertion is presented only to ask the platform about that caller. The
    scope ends with its request, also in the tasks that copied the request's context.
    """

    release_uid: str
    environment_uid: str
    cache: TeamMembershipCache = field(repr=False)
    cache_seconds: float
    assertion: CallerAssertion | None = field(default=None, repr=False)
    client: MainSequenceClient | None = field(default=None, repr=False)
    # The answers of this request, by key; the same question is asked once per request.
    answers: dict[AnswerKey, bool] = field(default_factory=dict, repr=False)
    active: bool = True


_current_scope: ContextVar[TeamMembershipScope | None] = ContextVar(
    "ms_tau_sdk_team_membership_scope",
    default=None,
)


def bind_team_membership_scope(
    scope: TeamMembershipScope | None,
) -> Token[TeamMembershipScope | None]:
    """Make ``scope`` the current request's; None for a request without a verified caller."""

    return _current_scope.set(scope)


def unbind_team_membership_scope(
    scope: TeamMembershipScope | None,
    token: Token[TeamMembershipScope | None],
) -> None:
    """End ``scope`` when its request ends, and restore the previous one."""

    if scope is not None:
        # Tasks that copied the request's context hold this same scope; it ends for them too.
        scope.active = False
        scope.assertion = None
        scope.client = None
        scope.answers = {}
    _current_scope.reset(token)


def canonical_team_uids(team_uids: Iterable[str | uuid.UUID]) -> list[str]:
    """Return the distinct canonical UIDs of ``team_uids``, sorted."""

    if isinstance(team_uids, str | bytes | uuid.UUID):
        raise TypeError("in_teams() takes a collection of team UIDs, not a single UID")
    try:
        return sorted({str(uuid.UUID(str(team_uid).strip())) for team_uid in team_uids})
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("Team UIDs must be valid UUIDs") from error


async def member_team_uids(
    person_uid: str,
    team_uids: Iterable[str | uuid.UUID],
    *,
    max_age_seconds: float | None = None,
) -> frozenset[str]:
    """Return which of ``team_uids`` the person ``person_uid`` is an active member of.

    ``VerifiedCaller.in_teams()`` and ``Requester.in_teams()`` call it, and it answers only about
    the verified caller of the current hosted request:

    - within the request, the same question is asked once;
    - across requests, an answer is reused while it is younger than the runtime's
      ``team_membership_cache_seconds``, or than ``max_age_seconds`` when the call gives one;
    - ``max_age_seconds=0`` always asks the platform, skips both kinds of reuse, and stores the
      new answer.

    A longer list is asked in calls of at most 100 teams. A failed call raises; it never answers
    "not a member".
    """

    asked = canonical_team_uids(team_uids)
    if max_age_seconds is not None and (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, int | float)
        or not math.isfinite(max_age_seconds)
        or max_age_seconds < 0
    ):
        raise ValueError("max_age_seconds must be None or a non-negative number of seconds")
    scope = _current_scope.get()
    if scope is None or not scope.active or scope.assertion is None:
        raise TauSDKError("in_teams() needs a hosted request whose caller assertion was verified")
    if person_uid != scope.assertion.caller.uid:
        raise TauSDKError("in_teams() answers only about the verified caller of the request")
    fresh = max_age_seconds == 0
    lifetime = scope.cache_seconds if max_age_seconds is None else max_age_seconds
    answers: dict[str, bool] = {}
    unanswered: list[str] = []
    for team_uid in asked:
        key = (scope.release_uid, scope.environment_uid, person_uid, team_uid)
        reused = None
        if not fresh:
            reused = scope.answers.get(key)
            if reused is None:
                reused = scope.cache.get(key, max_age=lifetime)
        if reused is None:
            unanswered.append(team_uid)
        else:
            answers[team_uid] = scope.answers[key] = reused
    for start in range(0, len(unanswered), MAX_TEAMS_PER_CALL):
        batch = unanswered[start : start + MAX_TEAMS_PER_CALL]
        members = await _platform_members(scope, person_uid, batch)
        for team_uid in batch:
            key = (scope.release_uid, scope.environment_uid, person_uid, team_uid)
            answers[team_uid] = scope.answers[key] = team_uid in members
            scope.cache.put(key, answers[team_uid])
    return frozenset(team_uid for team_uid, member in answers.items() if member)


async def _platform_members(
    scope: TeamMembershipScope,
    person_uid: str,
    team_uids: list[str],
) -> set[str]:
    assertion = scope.assertion if scope.active else None
    token = assertion.unexpired_token() if assertion is not None else None
    if token is None:
        raise TauSDKError(
            "in_teams() cannot ask the platform: the request's caller assertion has expired or "
            "the request has ended"
        )
    if scope.client is None:
        raise TauSDKError("in_teams() needs the Agent runtime of this process")
    return await scope.client.team_memberships(person_uid, team_uids, caller_assertion=token)


def deprecated_team_uids(signed: tuple[str, ...] | None) -> tuple[str, ...]:
    """Return the deprecated team list a caller assertion carried, with a warning.

    It raises when the assertion carried none; it never answers an empty list for that.
    """

    warnings.warn(
        "team_uids is deprecated; check membership with in_teams() instead",
        DeprecationWarning,
        stacklevel=3,
    )
    if signed is None:
        raise TauSDKError(
            "The caller assertion carries no team list; check membership with in_teams()"
        )
    return signed
