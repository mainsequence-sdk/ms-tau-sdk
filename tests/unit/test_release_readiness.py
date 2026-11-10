import asyncio
import time

import pytest
from structlog.testing import capture_logs

from ms_tau_sdk.backend.release_readiness import (
    DEADLINE_HEADER,
    ReleaseWarmth,
    caller_timeout_seconds,
    deadline_headers,
    expected_cold_start_seconds,
    ready_timeout_seconds,
    release_warmth,
    wait_until_ready,
)
from ms_tau_sdk.errors import ApplicationReadinessTimeoutError, ApplicationWakingError
from ms_tau_sdk.settings import TauSDKSettings

UID = "6f2c7c52-2b41-4c4e-9c43-36d2e0a8f1aa"


def _settings(**values) -> TauSDKSettings:
    return TauSDKSettings(_env_file=None, runtime_credential_id="credential-id", **values)


def test_a_configured_readiness_limit_wins_over_the_platforms_expectation():
    assert ready_timeout_seconds(_settings(), None) == 300
    assert ready_timeout_seconds(_settings(), 200) == 400
    assert ready_timeout_seconds(_settings(), 5) == 30
    assert ready_timeout_seconds(_settings(mcp_application_ready_timeout_seconds=45), 200) == 45
    assert ready_timeout_seconds(None, None) == 300


@pytest.mark.parametrize(
    ("scaling", "expected"),
    [
        ({"expected_cold_start_seconds": 120}, 120),
        ({"expected_cold_start_seconds": 0}, None),
        ({"expected_cold_start_seconds": "120"}, None),
        ({"expected_cold_start_seconds": True}, None),
        ({}, None),
        (None, None),
    ],
)
def test_only_a_positive_number_is_an_expected_start_time(scaling, expected):
    assert expected_cold_start_seconds(scaling) == expected


def test_a_release_is_warm_within_its_idle_window_less_a_margin():
    warmth = ReleaseWarmth()
    assert warmth.is_warm(UID) is False

    warmth.served(UID)
    assert warmth.is_warm(UID) is True
    # 50 seconds of the 60-second default window, less its 10-second margin, are used up.
    warmth._releases[UID].served -= 50
    assert warmth.is_warm(UID) is False

    warmth.served(UID, {"idle_timeout_seconds": 600})
    warmth._releases[UID].served -= 400
    assert warmth.is_warm(UID) is True
    warmth._releases[UID].served -= 100
    assert warmth.is_warm(UID) is False


def test_a_release_that_keeps_an_instance_stays_warm_until_forgotten():
    warmth = ReleaseWarmth()
    warmth.served(UID, {"min_instances": 1})
    warmth._releases[UID].served -= 100_000
    assert warmth.is_warm(UID) is True

    warmth.forget(UID)
    assert warmth.is_warm(UID) is False
    warmth.served(UID)
    # What the platform stated is kept across a forget.
    warmth._releases[UID].served -= 100_000
    assert warmth.is_warm(UID) is True


@pytest.mark.asyncio
async def test_the_wait_takes_its_limit_from_the_first_waking_answer():
    answers = iter(
        [
            ApplicationWakingError(retry_after_seconds=0.01, expected_cold_start_seconds=40),
            ApplicationWakingError(retry_after_seconds=0.01, expected_cold_start_seconds=900),
            "access",
        ]
    )

    async def obtain():
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    with capture_logs() as logs:
        access = await wait_until_ready(
            obtain, settings=_settings(), release_uid=UID, event="runtime.test"
        )

    assert access == "access"
    waiting = [log for log in logs if log["event"] == "runtime.test.waiting"]
    assert [log["ready_timeout_seconds"] for log in waiting] == [80, 80]
    ready = [log for log in logs if log["event"] == "runtime.test.ready"]
    assert len(ready) == 1 and ready[0]["cold_start_seconds"] >= 0.02
    assert release_warmth.last_cold_start_seconds(UID) >= 0.02


@pytest.mark.asyncio
async def test_a_release_that_stays_waking_ends_at_the_readiness_limit_without_a_cause():
    async def obtain():
        raise ApplicationWakingError(retry_after_seconds=0.05)

    started = time.monotonic()
    with pytest.raises(ApplicationReadinessTimeoutError) as raised:
        await wait_until_ready(
            obtain,
            settings=_settings(mcp_application_ready_timeout_seconds=0.2),
            release_uid=UID,
            event="runtime.test",
        )

    assert 0.18 <= time.monotonic() - started < 1
    assert raised.value.timeout_seconds == 0.2
    assert raised.value.__cause__ is None


@pytest.mark.asyncio
async def test_a_ready_release_is_not_waited_for_and_records_no_start():
    async def obtain():
        await asyncio.sleep(0)
        return "access"

    with capture_logs() as logs:
        assert (
            await wait_until_ready(obtain, settings=None, release_uid=UID, event="runtime.test")
            == "access"
        )

    assert logs == []
    assert release_warmth.last_cold_start_seconds(UID) is None


def test_the_deadline_header_carries_what_remains_in_milliseconds():
    assert deadline_headers(None) == {}
    headers = deadline_headers(time.monotonic() + 5)
    assert 4000 < int(headers[DEADLINE_HEADER]) <= 5000
    assert deadline_headers(time.monotonic() - 5) == {DEADLINE_HEADER: "1"}


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("5000", 5), (" 250 ", 0.25), ("0", None), ("-1", None), ("soon", None), (None, None)],
)
def test_only_a_positive_whole_number_of_milliseconds_is_a_caller_deadline(value, seconds):
    assert caller_timeout_seconds(value) == seconds
