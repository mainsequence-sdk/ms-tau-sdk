"""Phase A provenance stamps: one tau-native custom entry per user turn."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ms_tau_sdk.runtime.provenance import (
    PROVENANCE_NAMESPACE,
    CallerIdentityError,
    build_turn_provenance,
    turn_provenance_from_request,
    validate_caller_identity,
)
from ms_tau_sdk.runtime.session import ActiveSessionRuntime


def test_build_turn_provenance_maps_the_route_to_an_origin():
    assert build_turn_provenance("chat") == {"channel": "chat", "origin": "user"}
    assert build_turn_provenance("a2a") == {"channel": "a2a", "origin": "agent"}
    assert build_turn_provenance("responses") == {"channel": "responses", "origin": "agent"}
    with pytest.raises(ValueError):
        build_turn_provenance("smoke-signals")  # type: ignore[arg-type]


class _RecordingCodingSession:
    is_running = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def append_custom_entry(self, namespace: str, data: dict[str, object]) -> None:
        self.calls.append(("custom", (namespace, data)))

    async def prompt(self, content: str):
        self.calls.append(("prompt", content))
        yield {"type": "agent_settled"}

    def cancel(self) -> None:
        return None


async def _noop() -> None:
    return None


def _runtime(coding_session: _RecordingCodingSession) -> ActiveSessionRuntime:
    return ActiveSessionRuntime(
        session_uid="session-1",
        holder_id="holder-1",
        coding_session=coding_session,
        storage=SimpleNamespace(),
        provider=object(),
    )


async def _run(runtime: ActiveSessionRuntime, provenance: dict[str, str] | None) -> list[str]:
    events = []
    async for event in runtime.prompt(
        "hello",
        durability_task=lambda: asyncio.create_task(_noop()),
        provenance=provenance,
    ):
        events.append(event.type)
    return events


@pytest.mark.asyncio
async def test_prompt_writes_the_stamp_before_the_user_turn():
    coding_session = _RecordingCodingSession()
    runtime = _runtime(coding_session)

    events = await _run(runtime, build_turn_provenance("a2a"))

    assert coding_session.calls[0] == (
        "custom",
        (PROVENANCE_NAMESPACE, {"channel": "a2a", "origin": "agent"}),
    )
    assert coding_session.calls[1] == ("prompt", "hello")
    assert "agent_settled" in events


@pytest.mark.asyncio
async def test_prompt_without_provenance_writes_no_custom_entry():
    coding_session = _RecordingCodingSession()
    runtime = _runtime(coding_session)

    await _run(runtime, None)

    assert coding_session.calls == [("prompt", "hello")]


_USER_UID = "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d"
_AGENT_UID = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
_SERVICE_UID = "f0e1d2c3-b4a5-4968-8776-655443322110"
_SESSION_UID = "11111111-2222-4333-8444-555555555555"


def test_turn_provenance_from_request_for_a_human_caller():
    stamp = turn_provenance_from_request(
        "chat",
        {"x-caller-kind": "user", "x-user-uid": _USER_UID, "x-username": "jose"},
    )
    assert stamp == {
        "channel": "chat",
        "origin": "user",
        "actorKind": "user",
        "actorUid": _USER_UID,
        "actorName": "jose",
    }


def test_turn_provenance_from_request_for_an_agent_caller_on_the_a2a_route():
    stamp = turn_provenance_from_request(
        "a2a",
        {
            "X-Caller-Kind": "agent",
            "X-User-UID": _USER_UID,
            "X-Username": "jose",
            "X-Caller-Agent-UID": _AGENT_UID,
            "X-Caller-Coding-Agent-Service-UID": _SERVICE_UID,
            "X-Caller-Agent-Session-UID": _SESSION_UID,
        },
    )
    # Agent names are never stamped; the backend resolves them at projection.
    assert stamp == {
        "channel": "a2a",
        "origin": "agent",
        "actorKind": "agent",
        "actorUid": _AGENT_UID,
        "callerAgentSessionUid": _SESSION_UID,
    }


def test_origin_follows_the_caller_kind_not_the_route():
    stamp = turn_provenance_from_request("a2a", {"X-Caller-Kind": "user", "X-User-UID": _USER_UID})
    assert (stamp["channel"], stamp["origin"]) == ("a2a", "user")


@pytest.mark.parametrize(
    ("headers", "failing"),
    [
        ({}, ("X-Caller-Kind",)),
        ({"X-Caller-Kind": "system"}, ("X-Caller-Kind",)),
        ({"X-Caller-Kind": "user"}, ("X-User-UID",)),
        ({"X-Caller-Kind": "user", "X-User-UID": _USER_UID.upper()}, ("X-User-UID",)),
        (
            {"X-Caller-Kind": "user", "X-User-UID": _USER_UID, "X-Caller-Agent-UID": _AGENT_UID},
            ("X-Caller-Agent-UID",),
        ),
        (
            {"X-Caller-Kind": "user", "X-User-UID": _USER_UID, "X-Username": "x" * 256},
            ("X-Username",),
        ),
        (
            {"X-Caller-Kind": "agent"},
            ("X-Caller-Agent-UID", "X-Caller-Coding-Agent-Service-UID"),
        ),
        (
            {
                "X-Caller-Kind": "agent",
                "X-Caller-Agent-UID": _AGENT_UID,
                "X-Caller-Coding-Agent-Service-UID": _SERVICE_UID,
                "X-Caller-Agent-Session-UID": "nope",
            },
            ("X-Caller-Agent-Session-UID",),
        ),
    ],
    ids=[
        "absent",
        "unknown-kind",
        "user-without-uid",
        "user-non-canonical-uid",
        "user-with-agent-header",
        "username-too-long",
        "agent-missing-required",
        "agent-bad-session-uid",
    ],
)
def test_validate_caller_identity_rejects_each_violation(headers, failing):
    with pytest.raises(CallerIdentityError) as excinfo:
        validate_caller_identity(headers)
    assert excinfo.value.failing_headers == failing
    assert excinfo.value.status_code == 403
    assert excinfo.value.body() == {
        "detail": "Missing or invalid caller identity headers.",
        "code": "runtime_caller_identity_invalid",
    }


def test_validate_caller_identity_accepts_an_agent_without_a_delegation_parent():
    identity = validate_caller_identity(
        {
            "X-Caller-Kind": "agent",
            "X-Caller-Agent-UID": _AGENT_UID,
            "X-Caller-Coding-Agent-Service-UID": _SERVICE_UID,
        }
    )
    assert identity.kind == "agent"
    assert identity.caller_agent_session_uid is None
    assert identity.actor_uid == _AGENT_UID
