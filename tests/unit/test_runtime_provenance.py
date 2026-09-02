"""Phase A provenance stamps: one tau-native custom entry per user turn."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from astro.runtime.provenance import PROVENANCE_NAMESPACE, build_turn_provenance
from astro.runtime.session import ActiveSessionRuntime


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
