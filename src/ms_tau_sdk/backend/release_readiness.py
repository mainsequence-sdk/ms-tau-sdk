"""Readiness of the releases the runtime calls: one wait, one limit rule, one warm rule.

The contract is the one mainsequence-sdk follows (its ADR 0041). Readiness is asked of the
platform, never guessed: a release that is starting is waited for under a readiness limit, apart
from the limit of the call that follows. What this process remembers only spares a platform call
while a release is known warm.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import structlog

from ms_tau_sdk.errors import ApplicationReadinessTimeoutError, ApplicationWakingError
from ms_tau_sdk.settings import TauSDKSettings

logger = structlog.get_logger(__name__)

DEADLINE_HEADER = "Mainsequence-Request-Timeout-Ms"
DEFAULT_READY_TIMEOUT_SECONDS = 300.0
MIN_READY_TIMEOUT_SECONDS = 30.0
# Used until the platform states a release's own idle window.
DEFAULT_IDLE_TIMEOUT_SECONDS = 60.0
MIN_IDLE_MARGIN_SECONDS = 10.0
IDLE_MARGIN_FRACTION = 0.2


def _positive(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        seconds = float(value)
    except OverflowError:
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def expected_cold_start_seconds(scaling: object) -> float | None:
    """The start time the platform expects for a release, when it states one."""

    if not isinstance(scaling, Mapping):
        return None
    return _positive(scaling.get("expected_cold_start_seconds"))


def ready_timeout_seconds(
    settings: TauSDKSettings | None, expected_cold_start: float | None
) -> float:
    """How long a starting release is waited for.

    A limit set in the environment wins. Otherwise it is twice the start time the platform
    expects, at least 30 seconds, and the setting's default when the platform states none.
    """

    configured = (
        settings.mcp_application_ready_timeout_seconds
        if settings is not None
        else DEFAULT_READY_TIMEOUT_SECONDS
    )
    if (
        settings is not None
        and "mcp_application_ready_timeout_seconds" in settings.model_fields_set
    ):
        return configured
    if expected_cold_start is not None:
        return max(MIN_READY_TIMEOUT_SECONDS, 2 * expected_cold_start)
    return configured


@dataclass
class _Warmth:
    # Monotonic time of the last sign that the release was serving.
    served: float | None = None
    min_instances: int | None = None
    idle_timeout_seconds: float | None = None
    last_cold_start_seconds: float | None = None


class ReleaseWarmth:
    """What this process has seen of each release it calls. In memory, never the truth.

    Another caller may keep a release warm and a deployment may restart it. A wrong "warm" costs
    one held request, which is then classified; a wrong "cold" costs one platform call.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._releases: dict[str, _Warmth] = {}

    def served(self, release_uid: str, scaling: object = None) -> None:
        """Record a sign that the release serves: a ready answer, or a response below 500."""

        with self._lock:
            entry = self._releases.setdefault(release_uid, _Warmth())
            entry.served = time.monotonic()
            if isinstance(scaling, Mapping):
                minimum = scaling.get("min_instances")
                if isinstance(minimum, int) and not isinstance(minimum, bool) and minimum >= 0:
                    entry.min_instances = minimum
                entry.idle_timeout_seconds = (
                    _positive(scaling.get("idle_timeout_seconds")) or entry.idle_timeout_seconds
                )

    def cold_start(self, release_uid: str, seconds: float) -> None:
        with self._lock:
            self._releases.setdefault(release_uid, _Warmth()).last_cold_start_seconds = seconds

    def forget(self, release_uid: str) -> None:
        """Stop treating the release as warm; what the platform stated about it is kept."""

        with self._lock:
            entry = self._releases.get(release_uid)
            if entry is not None:
                entry.served = None

    def clear(self) -> None:
        with self._lock:
            self._releases.clear()

    def is_warm(self, release_uid: str) -> bool:
        """Whether a request sent now is expected to be served without a start."""

        with self._lock:
            entry = self._releases.get(release_uid)
            if entry is None or entry.served is None:
                return False
            if entry.min_instances is not None and entry.min_instances >= 1:
                return True
            idle = entry.idle_timeout_seconds or DEFAULT_IDLE_TIMEOUT_SECONDS
            margin = max(MIN_IDLE_MARGIN_SECONDS, IDLE_MARGIN_FRACTION * idle)
            return time.monotonic() - entry.served < idle - margin

    def last_cold_start_seconds(self, release_uid: str) -> float | None:
        with self._lock:
            entry = self._releases.get(release_uid)
            return entry.last_cold_start_seconds if entry is not None else None


release_warmth = ReleaseWarmth()


async def wait_until_ready[T](
    obtain: Callable[[], Awaitable[T]],
    *,
    settings: TauSDKSettings | None,
    release_uid: str,
    event: str,
    still_wanted: Callable[[], None] | None = None,
) -> T:
    """Call ``obtain`` until it stops raising ``ApplicationWakingError``.

    The readiness limit starts with the first call and is fixed by the first waking answer.
    ``still_wanted`` runs before every call and raises to end the wait. Log events are
    ``<event>.waiting`` and ``<event>.ready``. An expired wait raises
    ``ApplicationReadinessTimeoutError``, which keeps no cause.
    """

    started = asyncio.get_running_loop().time()
    deadline = asyncio.timeout(None)
    limit: float | None = None
    try:
        async with deadline:
            while True:
                if still_wanted is not None:
                    still_wanted()
                try:
                    access = await obtain()
                except ApplicationWakingError as error:
                    if limit is None:
                        limit = ready_timeout_seconds(settings, error.expected_cold_start_seconds)
                        deadline.reschedule(started + limit)
                    logger.info(
                        f"{event}.waiting",
                        resource_release_uid=release_uid,
                        runtime_access_state="waking",
                        retry_after_seconds=error.retry_after_seconds,
                        ready_timeout_seconds=limit,
                    )
                    await asyncio.sleep(error.retry_after_seconds)
                else:
                    if limit is not None:
                        seconds = asyncio.get_running_loop().time() - started
                        release_warmth.cold_start(release_uid, seconds)
                        logger.info(
                            f"{event}.ready",
                            resource_release_uid=release_uid,
                            cold_start_seconds=round(seconds, 3),
                        )
                    return access
    except TimeoutError:
        if not deadline.expired():
            raise
    raise ApplicationReadinessTimeoutError(timeout_seconds=limit or 0.0)


def deadline_headers(deadline: float | None) -> dict[str, str]:
    """The header that tells a release how long its caller will still wait.

    ``deadline`` is the monotonic instant by which the call must be answered; ``None`` sends
    nothing.
    """

    if deadline is None:
        return {}
    remaining = deadline - time.monotonic()
    return {DEADLINE_HEADER: str(max(1, math.floor(remaining * 1000)))}


def caller_timeout_seconds(value: str | None) -> float | None:
    """Seconds from a caller's deadline header; ``None`` when absent or unusable."""

    if value is None:
        return None
    try:
        milliseconds = int(value.strip())
    except ValueError:
        return None
    return milliseconds / 1000 if milliseconds > 0 else None


__all__ = [
    "DEADLINE_HEADER",
    "ReleaseWarmth",
    "caller_timeout_seconds",
    "deadline_headers",
    "expected_cold_start_seconds",
    "ready_timeout_seconds",
    "release_warmth",
    "wait_until_ready",
]
