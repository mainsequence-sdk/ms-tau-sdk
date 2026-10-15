"""Team membership on demand (issue #103, ADR-0019).

A hosted runtime answers ``in_teams()`` about the verified caller of a request by asking the
platform with that request's caller assertion. Within a request a question is asked once; across
requests an answer is reused for as long as the application chose; a fresh check always asks.
Answers are keyed by the release and Environment the runtime is deployed for, then the person and
the team. The platform's key set is served by the `served_platform_keys` fixture and its
membership check is a fake; nothing leaves the process.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI, Request
from pydantic import ValidationError

from ms_tau_sdk import create_app
from ms_tau_sdk.backend import team_memberships
from ms_tau_sdk.backend.assertions import ASSERTION_HEADER, CallerAssertion, VerifiedCaller
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.team_memberships import (
    MAX_CACHED_ANSWERS,
    MAX_TEAMS_PER_CALL,
    TeamMembershipCache,
    TeamMembershipScope,
    bind_team_membership_scope,
    unbind_team_membership_scope,
)
from ms_tau_sdk.errors import BackendError, TauSDKError
from ms_tau_sdk.runtime.requester import Requester

# The person the `platform_keys` fixture signs caller assertions for unless told otherwise.
OWNER_UID = "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d"
OTHER_UID = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"
TEAM_A = "8c7d6e5f-4a3b-4c2d-9e1f-0a9b8c7d6e5f"
TEAM_B = "1f2e3d4c-5b6a-4978-8a9b-0c1d2e3f4a5b"
# The release and Environment the `platform_keys` fixture deploys the runtime for, and others.
RELEASE_UID = "6f1c2b8e-4d3a-4e5f-9a8b-7c6d5e4f3a2b"
ENVIRONMENT_UID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
OTHER_RELEASE_UID = "44444444-5555-4666-8777-888888888888"
OTHER_ENVIRONMENT_UID = "99999999-8888-4777-8666-555555555555"
RUNTIME_ACCESS_TOKEN = "runtime-access-token-for-the-agent"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(team_memberships, "_now", clock)
    return clock


class _Platform:
    """The platform's membership check: which of the asked teams each person belongs to."""

    def __init__(self, members: dict[str, set[str]] | None = None) -> None:
        self.members = {OWNER_UID: {TEAM_A}} if members is None else members
        self.calls: list[tuple[str, list[str], str]] = []
        self.failure: Exception | None = None

    async def team_memberships(
        self, user_uid: str, team_uids: list[str], *, caller_assertion: str
    ) -> set[str]:
        self.calls.append((user_uid, list(team_uids), caller_assertion))
        if self.failure is not None:
            raise self.failure
        return set(team_uids) & self.members.get(user_uid, set())

    @property
    def asked(self) -> list[list[str]]:
        return [team_uids for _user_uid, team_uids, _assertion in self.calls]


def _app(platform_keys, tmp_path, platform: _Platform, **overrides: Any) -> FastAPI:
    app = create_app(platform_keys.hosted_settings(tmp_path, **overrides))
    # The application's platform client, which the application's lifespan creates.
    app.state.backend = platform

    @app.post("/in-teams")
    async def in_teams(request: Request) -> dict[str, Any]:
        """Ask the body's questions in order, about the caller or a requester."""

        body = await request.json()
        answers = []
        for question in body["questions"]:
            person = (
                Requester(uid=question["requester"])
                if "requester" in question
                else request.state.user
            )
            members = await person.in_teams(question["teams"], **question.get("options", {}))
            answers.append(sorted(members))
        return {"answers": answers}

    return app


async def _ask(http, platform_keys, questions: list[dict[str, Any]], **claims: Any) -> Any:
    response = await http.post(
        "/in-teams",
        json={"questions": questions},
        headers={ASSERTION_HEADER: platform_keys.caller_assertion(**claims)},
    )
    assert response.status_code == 200, response.text
    return response.json()["answers"]


@contextmanager
def _request(
    platform: _Platform | None,
    cache: TeamMembershipCache,
    *,
    cache_seconds: float = 0,
    release_uid: str = RELEASE_UID,
    environment_uid: str = ENVIRONMENT_UID,
    expires_at: int | None = None,
) -> Iterator[TeamMembershipScope]:
    """Bind what a hosted request with the verified caller OWNER_UID gives ``in_teams()``."""

    caller = VerifiedCaller(uid=OWNER_UID, is_organization_admin=False)
    scope = TeamMembershipScope(
        release_uid=release_uid,
        environment_uid=environment_uid,
        cache=cache,
        cache_seconds=cache_seconds,
        assertion=CallerAssertion(
            caller=caller,
            expires_at=int(time.time()) + 300 if expires_at is None else expires_at,
            token="raw-caller-assertion",
        ),
        client=platform,  # type: ignore[arg-type]
    )
    token = bind_team_membership_scope(scope)
    try:
        yield scope
    finally:
        unbind_team_membership_scope(scope, token)


THE_CALLER = VerifiedCaller(uid=OWNER_UID, is_organization_admin=False)


async def test_the_caller_learns_which_of_the_given_teams_it_belongs_to(
    served_platform_keys, tmp_path, asgi_client
):
    platform = _Platform()
    app = _app(served_platform_keys, tmp_path, platform)
    # The proof still signs a team list, which in_teams() never reads.
    raw = served_platform_keys.caller_assertion(team_uids=[TEAM_B])

    async with asgi_client(app) as http:
        response = await http.post(
            "/in-teams",
            json={"questions": [{"teams": [TEAM_B, TEAM_A.upper(), TEAM_A]}]},
            headers={ASSERTION_HEADER: raw},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"answers": [[TEAM_A]]}
    # One call, about the caller, for the distinct canonical teams, with the request's assertion.
    assert platform.calls == [(OWNER_UID, sorted([TEAM_A, TEAM_B]), raw)]


async def test_a_proof_without_a_team_list_is_enough(served_platform_keys, tmp_path, asgi_client):
    platform = _Platform()
    app = _app(served_platform_keys, tmp_path, platform)
    claims = served_platform_keys.caller_claims()
    del claims["team_uids"]

    async with asgi_client(app) as http:
        response = await http.post(
            "/in-teams",
            json={"questions": [{"teams": [TEAM_A, TEAM_B]}]},
            headers={ASSERTION_HEADER: served_platform_keys.sign(claims)},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"answers": [[TEAM_A]]}


async def test_within_a_request_the_same_question_is_asked_once(
    served_platform_keys, tmp_path, asgi_client
):
    platform = _Platform()
    app = _app(served_platform_keys, tmp_path, platform)

    async with asgi_client(app) as http:
        first = await _ask(
            http,
            served_platform_keys,
            [{"teams": [TEAM_A, TEAM_B]}, {"teams": [TEAM_B]}, {"teams": [TEAM_A]}],
        )
        second = await _ask(http, served_platform_keys, [{"teams": [TEAM_A]}])

    assert first == [[TEAM_A], [], [TEAM_A]]
    assert second == [[TEAM_A]]
    # The default lifetime, 0, reuses nothing across requests: the second request asks again.
    assert platform.asked == [sorted([TEAM_A, TEAM_B]), [TEAM_A]]


async def test_an_answer_is_reused_across_requests_while_younger_than_the_lifetime(
    served_platform_keys, tmp_path, asgi_client, clock
):
    platform = _Platform()
    app = _app(served_platform_keys, tmp_path, platform, team_membership_cache_seconds=60)

    async with asgi_client(app) as http:
        assert await _ask(http, served_platform_keys, [{"teams": [TEAM_A]}]) == [[TEAM_A]]
        # The person leaves the team. The application chose to accept answers up to 60 s old.
        platform.members[OWNER_UID] = set()
        clock.now += 59
        assert await _ask(http, served_platform_keys, [{"teams": [TEAM_A]}]) == [[TEAM_A]]
        clock.now += 1
        assert await _ask(http, served_platform_keys, [{"teams": [TEAM_A]}]) == [[]]

    assert platform.asked == [[TEAM_A], [TEAM_A]]


async def test_max_age_seconds_sets_the_lifetime_of_one_call(
    served_platform_keys, tmp_path, asgi_client, clock
):
    platform = _Platform()
    # The default lifetime: no reuse across requests unless a call asks for it.
    app = _app(served_platform_keys, tmp_path, platform)

    async with asgi_client(app) as http:
        await _ask(http, served_platform_keys, [{"teams": [TEAM_A]}])
        clock.now += 20
        reused = await _ask(
            http,
            served_platform_keys,
            [{"teams": [TEAM_A], "options": {"max_age_seconds": 30}}],
        )
        asked_again = await _ask(
            http,
            served_platform_keys,
            [{"teams": [TEAM_A], "options": {"max_age_seconds": 10}}],
        )

    assert reused == asked_again == [[TEAM_A]]
    assert len(platform.calls) == 2


async def test_a_fresh_check_always_asks_and_its_answer_is_kept(
    served_platform_keys, tmp_path, asgi_client, clock
):
    platform = _Platform()
    app = _app(served_platform_keys, tmp_path, platform, team_membership_cache_seconds=60)
    fresh = {"max_age_seconds": 0}

    async with asgi_client(app) as http:
        assert await _ask(http, served_platform_keys, [{"teams": [TEAM_A]}]) == [[TEAM_A]]
        platform.members[OWNER_UID] = set()
        answers = await _ask(
            http,
            served_platform_keys,
            [
                {"teams": [TEAM_A]},
                {"teams": [TEAM_A], "options": fresh},
                {"teams": [TEAM_A], "options": fresh},
                {"teams": [TEAM_A]},
            ],
        )
        later = await _ask(http, served_platform_keys, [{"teams": [TEAM_A]}])

    # The cached answer, then two fresh checks, each asking even though the request had an answer,
    # then the fresh answer, in this request and the next.
    assert answers == [[TEAM_A], [], [], []]
    assert later == [[]]
    assert platform.asked == [[TEAM_A], [TEAM_A], [TEAM_A]]


async def test_an_answer_about_one_person_never_answers_for_another(
    served_platform_keys, tmp_path, asgi_client
):
    platform = _Platform({OWNER_UID: {TEAM_A}, OTHER_UID: set()})
    app = _app(served_platform_keys, tmp_path, platform, team_membership_cache_seconds=60)

    async with asgi_client(app) as http:
        owner = await _ask(http, served_platform_keys, [{"teams": [TEAM_A]}])
        other = await _ask(http, served_platform_keys, [{"teams": [TEAM_A]}], user_uid=OTHER_UID)

    assert owner == [[TEAM_A]]
    assert other == [[]]
    assert [user_uid for user_uid, _teams, _assertion in platform.calls] == [OWNER_UID, OTHER_UID]


@pytest.mark.parametrize(
    "deployment",
    [
        pytest.param({"release_uid": OTHER_RELEASE_UID}, id="another_release"),
        pytest.param({"environment_uid": OTHER_ENVIRONMENT_UID}, id="another_environment"),
    ],
)
async def test_an_answer_never_crosses_releases_or_environments(deployment):
    platform = _Platform()
    cache = TeamMembershipCache()

    with _request(platform, cache, cache_seconds=60):
        assert await THE_CALLER.in_teams([TEAM_A]) == {TEAM_A}
    with _request(platform, cache, cache_seconds=60, **deployment):
        assert await THE_CALLER.in_teams([TEAM_A]) == {TEAM_A}
    with _request(platform, cache, cache_seconds=60):
        assert await THE_CALLER.in_teams([TEAM_A]) == {TEAM_A}

    # The first deployment's answer was reused for it alone.
    assert len(platform.calls) == 2


@pytest.mark.parametrize("count", [1_000, 10_000])
@pytest.mark.parametrize("person", ["caller", "requester"])
async def test_a_long_list_is_asked_in_calls_of_at_most_100_teams(
    served_platform_keys, tmp_path, asgi_client, count, person
):
    teams = sorted(str(uuid.uuid4()) for _ in range(count))
    platform = _Platform({OWNER_UID: set(teams[::7])})
    app = _app(served_platform_keys, tmp_path, platform)
    question: dict[str, Any] = {"teams": teams}
    if person == "requester":
        # A turn's requester who is the request's verified caller.
        question["requester"] = OWNER_UID

    async with asgi_client(app) as http:
        answers = await _ask(http, served_platform_keys, [question, question])

    assert answers == [sorted(teams[::7])] * 2
    assert len(platform.calls) == count // MAX_TEAMS_PER_CALL
    assert all(len(asked) <= MAX_TEAMS_PER_CALL for asked in platform.asked)
    assert [team for asked in platform.asked for team in asked] == teams


def test_the_cache_keeps_at_most_10000_answers_and_drops_the_oldest_first(clock):
    cache = TeamMembershipCache()
    keys = [
        (RELEASE_UID, ENVIRONMENT_UID, OWNER_UID, str(uuid.uuid4()))
        for _ in range(MAX_CACHED_ANSWERS + 1)
    ]

    for key in keys:
        cache.put(key, True)

    assert MAX_CACHED_ANSWERS == 10_000
    assert len(cache) == MAX_CACHED_ANSWERS
    assert cache.get(keys[0], max_age=60) is None
    assert cache.get(keys[1], max_age=60) is True
    assert cache.get(keys[-1], max_age=60) is True


async def test_in_teams_keeps_at_most_10000_answers_in_the_process(clock):
    teams = sorted(str(uuid.uuid4()) for _ in range(MAX_CACHED_ANSWERS + 50))
    platform = _Platform({OWNER_UID: set(teams)})
    cache = TeamMembershipCache()

    with _request(platform, cache, cache_seconds=60):
        assert await THE_CALLER.in_teams(teams) == set(teams)
    assert len(cache) == MAX_CACHED_ANSWERS
    platform.calls.clear()
    with _request(platform, cache, cache_seconds=60):
        await THE_CALLER.in_teams([teams[0], teams[-1]])

    # The oldest answer was dropped and is asked again; the newest is reused.
    assert platform.asked == [[teams[0]]]


async def test_a_requester_who_is_not_the_caller_is_refused(
    served_platform_keys, tmp_path, asgi_client
):
    platform = _Platform()
    app = _app(served_platform_keys, tmp_path, platform)

    async with asgi_client(app) as http:
        response = await http.post(
            "/in-teams",
            json={"questions": [{"teams": [TEAM_A], "requester": OTHER_UID}]},
            headers={ASSERTION_HEADER: served_platform_keys.caller_assertion()},
        )

    assert response.status_code == 500
    assert response.json()["error"] == "tau_sdk_error"
    assert "verified caller" in response.json()["message"]
    assert platform.calls == []


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(BackendError("Backend request failed", status_code=503), id="unavailable"),
        pytest.param(BackendError("Backend request failed", status_code=403), id="refused"),
    ],
)
async def test_a_failed_check_raises_and_never_answers_not_a_member(
    served_platform_keys, tmp_path, asgi_client, failure
):
    platform = _Platform()
    platform.failure = failure
    app = _app(served_platform_keys, tmp_path, platform, team_membership_cache_seconds=60)

    async with asgi_client(app) as http:
        failed = await http.post(
            "/in-teams",
            json={"questions": [{"teams": [TEAM_A]}]},
            headers={ASSERTION_HEADER: served_platform_keys.caller_assertion()},
        )
        platform.failure = None
        answers = await _ask(http, served_platform_keys, [{"teams": [TEAM_A]}])

    assert failed.status_code == 502
    assert failed.json()["error"] == "backend_error"
    # Nothing was kept from the failed call: the next request asks again.
    assert answers == [[TEAM_A]]
    assert len(platform.calls) == 2


async def test_outside_a_hosted_request_in_teams_raises():
    with pytest.raises(TauSDKError, match="hosted request"):
        await THE_CALLER.in_teams([TEAM_A])
    with pytest.raises(TauSDKError, match="hosted request"):
        await Requester(uid=OWNER_UID).in_teams([TEAM_A])


async def test_in_teams_ends_with_its_request_also_in_tasks_that_copied_it():
    platform = _Platform()
    request_over = asyncio.Event()

    async def later() -> frozenset[str]:
        await request_over.wait()
        return await THE_CALLER.in_teams([TEAM_A])

    with _request(platform, TeamMembershipCache()):
        child = asyncio.create_task(later())
    request_over.set()

    with pytest.raises(TauSDKError, match="hosted request"):
        await child
    assert platform.calls == []


async def test_an_expired_assertion_is_never_presented():
    platform = _Platform()

    with _request(platform, TeamMembershipCache(), expires_at=int(time.time()) - 1):
        with pytest.raises(TauSDKError, match="expired"):
            await THE_CALLER.in_teams([TEAM_A])

    assert platform.calls == []


async def test_without_the_runtimes_platform_client_in_teams_raises():
    with _request(None, TeamMembershipCache()):
        with pytest.raises(TauSDKError, match="Agent runtime"):
            await THE_CALLER.in_teams([TEAM_A])


@pytest.mark.parametrize(
    ("team_uids", "error"),
    [
        pytest.param(TEAM_A, TypeError, id="one_uid_string"),
        pytest.param(uuid.UUID(TEAM_A), TypeError, id="one_uuid"),
        pytest.param(["team-1"], ValueError, id="not_a_uuid"),
        pytest.param([None], ValueError, id="none"),
    ],
)
async def test_team_uids_must_be_a_collection_of_uuids(team_uids, error):
    platform = _Platform()

    with _request(platform, TeamMembershipCache()):
        with pytest.raises(error):
            await THE_CALLER.in_teams(team_uids)

    assert platform.calls == []


@pytest.mark.parametrize("max_age_seconds", [-1, math.inf, math.nan, True, "60"])
async def test_max_age_seconds_must_be_a_non_negative_number(max_age_seconds):
    platform = _Platform()

    with _request(platform, TeamMembershipCache()):
        with pytest.raises(ValueError, match="max_age_seconds"):
            await THE_CALLER.in_teams([TEAM_A], max_age_seconds=max_age_seconds)

    assert platform.calls == []


async def test_uuid_objects_and_an_empty_list_are_accepted():
    platform = _Platform()

    with _request(platform, TeamMembershipCache()):
        assert await THE_CALLER.in_teams([uuid.UUID(TEAM_A)]) == {TEAM_A}
        assert await THE_CALLER.in_teams([]) == frozenset()

    assert platform.asked == [[TEAM_A]]


def test_the_lifetime_is_the_applications_setting(platform_keys, tmp_path, monkeypatch):
    assert platform_keys.hosted_settings(tmp_path).team_membership_cache_seconds == 0
    monkeypatch.setenv("TAU_TEAM_MEMBERSHIP_CACHE_SECONDS", "30")

    assert platform_keys.hosted_settings(tmp_path).team_membership_cache_seconds == 30


@pytest.mark.parametrize("value", ["-1", "inf", "nan"])
def test_the_lifetime_is_a_non_negative_finite_number(platform_keys, tmp_path, monkeypatch, value):
    monkeypatch.setenv("TAU_TEAM_MEMBERSHIP_CACHE_SECONDS", value)

    with pytest.raises(ValidationError):
        platform_keys.hosted_settings(tmp_path)


def test_the_requesters_team_list_is_deprecated():
    with pytest.warns(DeprecationWarning, match="in_teams"):
        assert Requester(uid=OWNER_UID, team_uids=(TEAM_A,)).team_uids == (TEAM_A,)
    # A requester the turn knows only from the platform's record keeps an empty list.
    with pytest.warns(DeprecationWarning, match="in_teams"):
        assert Requester(uid=OWNER_UID).team_uids == ()
    # A requester whose caller assertion carried no list has no list to read.
    with pytest.warns(DeprecationWarning, match="in_teams"):
        with pytest.raises(TauSDKError, match=r"in_teams\(\)"):
            Requester(uid=OWNER_UID, team_uids=None).team_uids  # noqa: B018


class _RuntimeAuth:
    """The runtime's own credential."""

    def bind_client(self, client: httpx.AsyncClient) -> None:
        del client

    async def headers(self, *, force: bool = False) -> dict[str, str]:
        del force
        return {"Authorization": f"Bearer {RUNTIME_ACCESS_TOKEN}"}

    async def prefetch(self) -> None:
        return None


async def test_the_platform_is_asked_with_the_runtime_credential_and_the_assertion(
    platform_keys, tmp_path
):
    client = MainSequenceClient(platform_keys.hosted_settings(tmp_path), _RuntimeAuth())  # type: ignore[arg-type]
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # The platform leaves out what it does not count; a team it was not asked about is ignored.
        return httpx.Response(
            200, json={"user_uid": OWNER_UID, "team_uids": [TEAM_A, str(uuid.uuid4())]}
        )

    with respx.mock(assert_all_called=False) as router:
        router.route(host="backend").mock(side_effect=answer)
        members = await client.team_memberships(
            OWNER_UID, [TEAM_A, TEAM_B], caller_assertion="raw-caller-assertion"
        )
    await client.aclose()

    assert members == {TEAM_A}
    [request] = seen
    assert request.method == "GET"
    assert request.url.path == f"/api/v1/users/{OWNER_UID}/team-memberships/"
    assert request.url.params["team_uid__in"] == f"{TEAM_A},{TEAM_B}"
    assert request.headers["Authorization"] == f"Bearer {RUNTIME_ACCESS_TOKEN}"
    assert request.headers[ASSERTION_HEADER] == "raw-caller-assertion"


@pytest.mark.parametrize(
    ("status_code", "body", "backend_status"),
    [
        pytest.param(403, {"detail": "Not this person."}, 403, id="refused"),
        pytest.param(404, {"detail": "Not found."}, 404, id="no_check"),
        pytest.param(200, {"user_uid": OWNER_UID}, None, id="no_list"),
        pytest.param(200, {"team_uids": TEAM_A}, None, id="not_a_list"),
        pytest.param(200, {"team_uids": [1]}, None, id="not_uids"),
    ],
)
async def test_a_refusal_or_an_invalid_answer_raises_the_api_error(
    platform_keys, tmp_path, status_code, body, backend_status
):
    client = MainSequenceClient(platform_keys.hosted_settings(tmp_path), _RuntimeAuth())  # type: ignore[arg-type]

    with respx.mock(assert_all_called=False) as router:
        router.route(host="backend").mock(return_value=httpx.Response(status_code, json=body))
        with pytest.raises(BackendError) as raised:
            await client.team_memberships(
                OWNER_UID, [TEAM_A], caller_assertion="raw-caller-assertion"
            )
    await client.aclose()

    assert raised.value.backend_status == backend_status
    assert "raw-caller-assertion" not in str(raised.value)
