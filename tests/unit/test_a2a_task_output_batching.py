"""A Task attempt's text answer reaches the platform in a few ordered writes when it is slow (#116).

The fake platform answers every call after ``LATENCY``, longer than the flush interval, while the
model streams many small text deltas. The turn keeps reading the model while the platform answers,
so text that arrives meanwhile joins the next write instead of becoming one write per delta.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from tau_agent.events import MessageEndEvent, MessageUpdateEvent
from tau_agent.messages import AssistantMessage
from tau_agent.provider_events import TextDeltaEvent
from tau_coding.events import SessionAgentEndEvent

from ms_tau_sdk.api.a2a import _execute_task, _output_contract, _stream_task_events
from ms_tau_sdk.backend.models import (
    AgentTask,
    AgentTaskDispatch,
    AgentTaskExecutionAttempt,
)
from ms_tau_sdk.errors import BackendError
from ms_tau_sdk.runtime.events import translate_tau_event
from ms_tau_sdk.settings import TauSDKSettings

LATENCY = 0.06
FLUSH_INTERVAL_MS = 25
MODEL_GAP = 0.005
DELTAS = [f"word{index} " for index in range(40)]
ANSWER = "".join(DELTAS)
CONFIG = TauSDKSettings(
    _env_file=None,
    a2a_task_output_flush_interval_ms=FLUSH_INTERVAL_MS,
    a2a_task_event_poll_interval_seconds=0.05,
)


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=text,
        api="responses",
        provider="openai-codex",
        model="gpt-5.3-codex-spark",
        stop_reason="stop",
    )


def _model_events(deltas: list[str] = DELTAS) -> list[Any]:
    events: list[Any] = []
    streamed = ""
    for delta in deltas:
        streamed += delta
        partial = _assistant(streamed)
        events.append(
            MessageUpdateEvent(
                message=partial,
                assistant_message_event=TextDeltaEvent(
                    content_index=0,
                    delta=delta,
                    partial=partial,
                ),
            )
        )
    final = _assistant(streamed)
    events.append(MessageEndEvent(message=final))
    events.append(SessionAgentEndEvent(messages=[final], will_retry=False))
    return events


def _task(*, status: str = "working", cancellation_requested: bool = False) -> AgentTask:
    return AgentTask(
        uid="backend-task-1",
        task_id="task-1",
        context_id="session-1",
        agent_session_uid="session-1",
        agent_uid="agent-1",
        status=status,
        cancellation_requested=cancellation_requested,
    )


class _SlowPlatform:
    """The platform's Task API for one attempt, answering every call after ``LATENCY``."""

    def __init__(self, *, cancel_after_writes: int | None = None) -> None:
        self.cancel_after_writes = cancel_after_writes
        self.calls: list[tuple[str, str]] = []
        self.revision = 0
        self.settled: str | None = None

    async def _answer(self, operation: str, text: str = "") -> None:
        await asyncio.sleep(LATENCY)
        # Nothing reaches the platform for the attempt once it has settled.
        assert self.settled is None or operation == "read", (operation, self.settled)
        self.calls.append((operation, text))

    @property
    def writes(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0] in {"create", "append", "finalize"}]

    @property
    def reads(self) -> int:
        return sum(call[0] == "read" for call in self.calls)

    async def list_task_dispatches(self, _task_uid: str) -> list[AgentTaskDispatch]:
        return [AgentTaskDispatch(uid="dispatch-1", state="pending")]

    async def claim_task_dispatch(self, _task_uid: str, **_: Any) -> AgentTaskExecutionAttempt:
        return AgentTaskExecutionAttempt(
            uid="attempt-1",
            dispatch_uid="dispatch-1",
            attempt_number=1,
            state="claimed",
        )

    async def start_task_attempt(self, _task_uid: str, **_: Any) -> AgentTaskExecutionAttempt:
        return AgentTaskExecutionAttempt(
            uid="attempt-1",
            dispatch_uid="dispatch-1",
            attempt_number=1,
            state="running",
        )

    async def get_task(self, _task_uid: str, **_: Any) -> AgentTask:
        await self._answer("read")
        if self.settled is not None:
            return _task(status=self.settled)
        canceled = (
            self.cancel_after_writes is not None and len(self.writes) >= self.cancel_after_writes
        )
        return _task(cancellation_requested=canceled)

    async def list_task_messages(self, _task: AgentTask, **_: Any) -> list[dict[str, Any]]:
        return []

    async def create_task_output(self, _task_uid: str, *, parts, **_: Any) -> dict[str, Any]:
        await self._answer("create", "".join(part["text"] for part in parts))
        self.revision = 1
        return {"uid": "output-1", "revision": self.revision}

    async def append_task_output(
        self, _task_uid: str, *, expected_revision: int, parts, **_: Any
    ) -> dict[str, Any]:
        assert expected_revision == self.revision
        await self._answer("append", "".join(part["text"] for part in parts))
        self.revision += 1
        return {"uid": "output-1", "revision": self.revision}

    async def finalize_task_output(
        self, _task_uid: str, *, expected_revision: int, **_: Any
    ) -> dict[str, Any]:
        assert expected_revision == self.revision
        await self._answer("finalize")
        self.revision += 1
        return {"uid": "output-1", "revision": self.revision}

    async def settle_task_attempt(self, _task_uid: str, *, status: str, **_: Any) -> Any:
        await self._answer("settle", status)
        self.settled = status
        return SimpleNamespace(state=status)


class _FailingWrites(_SlowPlatform):
    async def append_task_output(self, _task_uid: str, **_: Any) -> dict[str, Any]:
        await asyncio.sleep(LATENCY)
        raise BackendError("Backend Task output append failed")


class _StreamingModel:
    """A session runtime whose turn streams ``DELTAS`` as the installed Tau runtime emits them."""

    settings = CONFIG
    draining = False

    def __init__(self, deltas: list[str] = DELTAS) -> None:
        self.events = _model_events(deltas)
        self.consumed = 0

    async def task_execution_fence(self, _session_uid: str) -> SimpleNamespace:
        return SimpleNamespace(holder_id="holder-1", lease_token="lease-1")

    async def prompt(self, _context_id: str, _prompt: str, *, provenance=None, turn_uid=None):
        for event in self.events:
            await asyncio.sleep(MODEL_GAP)
            self.consumed += 1
            yield translate_tau_event(event)

    def mark_response_delivered(self, _session_uid: str) -> bool:
        return True


async def _run(platform: _SlowPlatform, model: _StreamingModel) -> dict[str, Any]:
    return await _execute_task(
        platform,  # type: ignore[arg-type]
        model,  # type: ignore[arg-type]
        _task(status="submitted"),
        prompt="Read the CRM settings.",
        output_contract=_output_contract({}),
        max_output_bytes=64 * 1024,
        provenance={"channel": "a2a", "origin": "agent"},
        config=CONFIG,
    )


@pytest.mark.asyncio
async def test_a_slow_platform_gets_the_answer_in_a_few_ordered_writes():
    platform = _SlowPlatform()

    result = await _run(platform, _StreamingModel())

    # The answer Message carries the final text, trimmed; the output keeps it as streamed.
    assert result["parts"] == [{"text": ANSWER.strip()}]
    writes = platform.writes
    assert writes[0][0] == "create"
    assert writes[-1] == ("finalize", "")
    assert {operation for operation, _text in writes[1:-1]} <= {"append"}
    assert "".join(text for _operation, text in writes) == ANSWER
    # One write per delta would be 40 writes and at least as many reads of the Task.
    assert len(writes) <= 10
    assert platform.reads <= 30
    assert platform.settled == "completed"


@pytest.mark.asyncio
async def test_a_slow_platform_does_not_hold_back_the_streamed_answer():
    platform = _SlowPlatform()
    model = _StreamingModel()

    events = [
        event
        async for event in _stream_task_events(
            platform,  # type: ignore[arg-type]
            model,  # type: ignore[arg-type]
            _task(status="submitted"),
            prompt="Read the CRM settings.",
            output_contract=_output_contract({}),
            max_output_bytes=64 * 1024,
            provenance={"channel": "a2a", "origin": "agent"},
            config=CONFIG,
        )
    ]

    streamed = [
        part["text"]
        for event in events
        if "artifactUpdate" in event
        for part in event["artifactUpdate"]["artifact"]["parts"]
    ]
    # The caller sees every delta as it arrives; the platform gets them in a few writes.
    assert "".join(streamed) == ANSWER
    assert len([text for text in streamed if text]) == len(DELTAS)
    assert "".join(text for _operation, text in platform.writes) == ANSWER
    assert len(platform.writes) <= 10
    assert events[-1]["final"] is True
    assert platform.settled == "completed"


@pytest.mark.asyncio
async def test_a_cancellation_seen_in_the_background_stops_the_attempt_before_it_settles():
    platform = _SlowPlatform(cancel_after_writes=1)
    # A model that would stream for five seconds, far longer than the background read takes to
    # see the cancellation, so the turn stops reading it whatever the machine's speed.
    model = _StreamingModel([f"word{index} " for index in range(1000)])

    result = await _run(platform, model)

    assert result == {"taskId": "task-1", "state": "canceled"}
    assert platform.settled == "canceled"
    # The turn stopped reading the model once the cancellation was seen.
    assert model.consumed < len(model.events) // 2
    assert "finalize" not in {operation for operation, _text in platform.writes}


@pytest.mark.asyncio
async def test_a_failed_background_write_ends_the_attempt_and_nothing_keeps_running():
    platform = _FailingWrites()

    with pytest.raises(BackendError, match="append failed"):
        await _run(platform, _StreamingModel())

    calls = len(platform.calls)
    await asyncio.sleep(LATENCY * 3)
    # No read or write the writer started is still running after the attempt ended.
    assert len(platform.calls) == calls
    assert platform.settled is None
