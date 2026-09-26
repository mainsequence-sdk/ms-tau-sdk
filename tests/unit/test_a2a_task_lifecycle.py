import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from ms_tau_sdk.api.a2a import (
    _execute_task,
    _message_stream_response,
    _output_contract,
    _stream_task_events,
    _task_payload,
    _task_subscription_events,
    _wait_for_task_return_state,
    task_caller_delivery_available,
    task_dispatch_available,
)
from ms_tau_sdk.backend.models import (
    AgentTask,
    AgentTaskDispatch,
    AgentTaskEvent,
    AgentTaskEventPage,
    AgentTaskExecutionAttempt,
    AgentTaskSnapshot,
)
from ms_tau_sdk.errors import BackendError, TaskTerminalizationUnknownError
from ms_tau_sdk.protocols.a2a_failure import (
    TASK_FAILURE_EXTENSION_URI,
    TASK_STATUS_DETAIL_EXTENSION_URI,
)
from ms_tau_sdk.runtime.events import TauRuntimeEvent
from ms_tau_sdk.runtime.task_context import (
    TaskExecutionContext,
    active_task_execution,
    task_execution_scope,
)
from ms_tau_sdk.settings import TauSDKSettings
from ms_tau_sdk.tools.task_control import create_task_control_tools


def _task(status: str = "submitted", *, cancellation_requested: bool = False) -> AgentTask:
    return AgentTask(
        uid="backend-task-1",
        task_id="task-1",
        context_id="session-1",
        agent_session_uid="session-1",
        agent_uid="agent-1",
        status=status,
        cancellation_requested=cancellation_requested,
    )


def _claimed_client(task: AgentTask) -> AsyncMock:
    client = AsyncMock()
    claimed = AgentTaskExecutionAttempt(
        uid="attempt-1",
        dispatch_uid="dispatch-1",
        attempt_number=1,
        state="claimed",
    )
    client.list_task_dispatches.return_value = [
        AgentTaskDispatch(uid="dispatch-1", state="pending")
    ]
    client.claim_task_dispatch.return_value = claimed
    client.start_task_attempt.return_value = claimed.model_copy(update={"state": "running"})
    client.get_task.return_value = task
    return client


def _completed_output_client(client: AsyncMock) -> None:
    client.create_task_output.return_value = {"uid": "output-1", "revision": 1}
    client.append_task_output.return_value = {"uid": "output-1", "revision": 2}
    client.finalize_task_output.return_value = {"uid": "output-1", "revision": 2}


class _ExecutionManager:
    settings = TauSDKSettings(_env_file=None)

    def __init__(self, *, draining: bool) -> None:
        self.draining = draining
        self.prompt_started = False
        self.delivered: list[str] = []

    async def task_execution_fence(self, _session_uid: str):
        return SimpleNamespace(holder_id="holder-1", lease_token="lease-1")

    async def prompt(self, _context_id: str, _prompt: str, *, provenance=None, turn_uid=None):
        self.prompt_started = True
        raise asyncio.CancelledError
        yield  # pragma: no cover

    def mark_response_delivered(self, session_uid: str) -> bool:
        self.delivered.append(session_uid)
        return True


async def test_shutdown_does_not_translate_executor_cancellation_to_task_cancellation():
    task = _task("working")
    client = _claimed_client(task)
    manager = _ExecutionManager(draining=True)

    with pytest.raises(asyncio.CancelledError):
        await _execute_task(
            client,
            manager,  # type: ignore[arg-type]
            task,
            prompt="Do durable work.",
            output_contract=_output_contract({}),
            max_output_bytes=1024,
            provenance={"channel": "a2a", "origin": "agent"},
            config=manager.settings,
        )

    assert manager.prompt_started is True
    client.settle_task_attempt.assert_not_awaited()


async def test_backend_outage_leaves_attempt_for_durable_recovery():
    task = _task("working")
    client = _claimed_client(task)

    class Manager(_ExecutionManager):
        async def prompt(self, _context_id: str, _prompt: str, *, provenance=None, turn_uid=None):
            raise BackendError("backend unavailable")
            yield  # pragma: no cover

    manager = Manager(draining=False)
    with pytest.raises(BackendError, match="backend unavailable"):
        await _execute_task(
            client,
            manager,  # type: ignore[arg-type]
            task,
            prompt="Do durable work.",
            output_contract=_output_contract({}),
            max_output_bytes=1024,
            provenance={"channel": "a2a", "origin": "agent"},
            config=manager.settings,
        )

    client.settle_task_attempt.assert_not_awaited()


async def test_durable_cancellation_intent_is_settled_before_agent_execution():
    task = _task("working", cancellation_requested=True)
    canceled = _task("canceled", cancellation_requested=True)
    client = _claimed_client(task)
    client.settle_task_attempt.return_value = canceled

    async def get_task(_task_uid):
        return canceled if client.settle_task_attempt.await_count else task

    client.get_task.side_effect = get_task
    manager = _ExecutionManager(draining=False)

    result = await _execute_task(
        client,
        manager,  # type: ignore[arg-type]
        task,
        prompt="Do durable work.",
        output_contract=_output_contract({}),
        max_output_bytes=1024,
        provenance={"channel": "a2a", "origin": "agent"},
        config=manager.settings,
    )

    assert result == {"taskId": "task-1", "state": "canceled"}
    assert manager.prompt_started is False
    client.settle_task_attempt.assert_awaited_once_with(
        task.uid,
        attempt_uid="attempt-1",
        holder_id="holder-1",
        lease_token="lease-1",
        status="canceled",
    )
    client.create_task_output.assert_not_awaited()
    client.append_task_output.assert_not_awaited()
    client.finalize_task_output.assert_not_awaited()


async def test_dispatch_signal_pulls_durable_task_then_claims_before_execution():
    submitted = _task().model_copy(
        update={
            "dispatch_uid": "dispatch-1",
            "latest_message": {
                "messageId": "message-1",
                "role": "ROLE_REQUESTER",
                "parts": [{"text": "Do durable work."}],
            },
            "metadata": {
                "execution": {
                    "output_contract": {
                        "mode": "none",
                        "schema": None,
                        "repair_attempts": 3,
                    },
                    "provenance": {"channel": "a2a", "origin": "agent"},
                }
            },
        }
    )
    working = submitted.model_copy(update={"status": "working"})
    completed = submitted.model_copy(update={"status": "completed"})
    client = _claimed_client(working)
    get_count = 0

    async def get_task(_task_uid):
        nonlocal get_count
        get_count += 1
        if client.settle_task_attempt.await_count:
            return completed
        return submitted if get_count == 1 else working

    client.get_task.side_effect = get_task
    _completed_output_client(client)
    client.settle_task_attempt.return_value = completed

    class Manager:
        settings = TauSDKSettings(_env_file=None)
        draining = False

        def __init__(self) -> None:
            self.background = None
            self.prompt_started = False

        def create_a2a_task_execution(self, task_uid, coroutine, *, name):
            assert task_uid == submitted.uid
            assert name == "a2a-task-task-1"
            assert client.claim_task_dispatch.await_count == 1
            self.background = coroutine
            return None, True

        async def task_execution_fence(self, _session_uid):
            return SimpleNamespace(holder_id="holder-1", lease_token="lease-1")

        async def prompt(self, _context_id, _prompt, *, provenance=None, turn_uid=None):
            self.prompt_started = True
            assert client.claim_task_dispatch.await_count == 1
            yield TauRuntimeEvent(
                type="message_end",
                data={
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Durable result."}],
                        "stopReason": "stop",
                    }
                },
            )

        def mark_response_delivered(self, _session_uid):
            return True

    manager = Manager()
    result = await task_dispatch_available(
        {"task_uid": submitted.uid, "dispatch_uid": "dispatch-1"},
        client,
        manager,  # type: ignore[arg-type]
        manager.settings,
    )

    assert result == {
        "accepted": True,
        "scheduled": True,
        "task_uid": submitted.uid,
    }
    assert manager.background is not None
    assert manager.prompt_started is False
    assert client.claim_task_dispatch.await_count == 1
    await manager.background
    assert manager.prompt_started is True
    client.claim_task_dispatch.assert_awaited_once_with(
        submitted.uid,
        holder_id="holder-1",
        lease_token="lease-1",
        dispatch_uid="dispatch-1",
        executor_instance_id="holder-1",
    )


async def test_attempt_writes_refresh_the_rotating_canonical_session_lease_token():
    working = _task("working")
    completed = _task("completed")
    client = _claimed_client(working)
    _completed_output_client(client)
    client.settle_task_attempt.return_value = completed
    client.get_task.return_value = completed

    class Manager:
        settings = TauSDKSettings(_env_file=None)
        draining = False

        def __init__(self) -> None:
            self.fence_count = 0

        async def task_execution_fence(self, _session_uid):
            self.fence_count += 1
            return SimpleNamespace(
                holder_id="holder-1",
                lease_token=f"lease-{self.fence_count}",
            )

        async def prompt(self, _context_id, _prompt, *, provenance=None, turn_uid=None):
            yield TauRuntimeEvent(
                type="message_end",
                data={
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Done."}],
                        "stopReason": "stop",
                    }
                },
            )

        def mark_response_delivered(self, _session_uid):
            return True

    manager = Manager()
    await _execute_task(
        client,
        manager,  # type: ignore[arg-type]
        working,
        prompt="Do it.",
        output_contract=_output_contract({}),
        max_output_bytes=1024,
        provenance={"channel": "a2a", "origin": "agent"},
        config=manager.settings,
    )

    assert client.claim_task_dispatch.await_args.kwargs["lease_token"] == "lease-1"
    assert client.start_task_attempt.await_args.kwargs["lease_token"] == "lease-2"
    assert client.create_task_output.await_args.kwargs["lease_token"] == "lease-3"
    assert client.finalize_task_output.await_args.kwargs["lease_token"] == "lease-4"
    assert client.settle_task_attempt.await_args.kwargs["lease_token"] == "lease-5"


async def test_structured_interruption_settles_attempt_without_requiring_text_output():
    working = _task("working")
    interrupted = _task("input_required")
    client = _claimed_client(working)
    client.settle_task_attempt.return_value = interrupted
    client.get_task.return_value = interrupted

    class Manager:
        settings = TauSDKSettings(_env_file=None)
        draining = False

        async def task_execution_fence(self, _session_uid):
            return SimpleNamespace(holder_id="holder-1", lease_token="lease-1")

        async def prompt(self, _context_id, _prompt, *, provenance=None, turn_uid=None):
            context = active_task_execution()
            assert context is not None
            context.request_interruption(
                status="input_required",
                text="Which account should be used?",
                details={
                    "reason": "missing_account",
                    "inputSchema": {"type": "string"},
                },
            )
            return
            yield  # pragma: no cover

        def mark_response_delivered(self, _session_uid):
            return True

    manager = Manager()
    result = await _execute_task(
        client,
        manager,  # type: ignore[arg-type]
        working,
        prompt="Do it.",
        output_contract=_output_contract({}),
        max_output_bytes=1024,
        provenance={"channel": "a2a", "origin": "agent"},
        config=manager.settings,
    )

    assert result == {"taskId": "task-1", "state": "input_required"}
    settlement = client.settle_task_attempt.await_args
    assert settlement.args == (working.uid,)
    assert settlement.kwargs["status"] == "input_required"
    status_message = settlement.kwargs["status_message"]
    assert status_message["role"] == "ROLE_RESPONDER"
    assert status_message["parts"] == [{"text": "Which account should be used?"}]
    assert status_message["extensions"] == [TASK_STATUS_DETAIL_EXTENSION_URI]
    client.create_task_output.assert_not_awaited()
    client.append_task_output.assert_not_awaited()
    client.finalize_task_output.assert_not_awaited()


async def test_streaming_terminal_agent_failure_settles_task_failed_not_completed():
    working = _task("working")
    failed = _task("failed")
    client = _claimed_client(working)
    client.settle_task_attempt.return_value = failed
    client.get_task.return_value = failed

    class Manager:
        settings = TauSDKSettings(_env_file=None)
        draining = False

        async def task_execution_fence(self, _session_uid):
            return SimpleNamespace(holder_id="holder-1", lease_token="lease-1")

        async def prompt(self, _context_id, _prompt, *, provenance=None, turn_uid=None):
            yield TauRuntimeEvent(
                type="message_end",
                data={
                    "message": {
                        "role": "assistant",
                        "content": [],
                        "stopReason": "error",
                        "errorMessage": "Provider failed.",
                    }
                },
            )

    events = [
        event
        async for event in _stream_task_events(
            client,
            Manager(),  # type: ignore[arg-type]
            working,
            prompt="Do it.",
            output_contract=_output_contract({}),
            max_output_bytes=1024,
            provenance={"channel": "a2a", "origin": "agent"},
            history_length=0,
        )
    ]

    assert events[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_FAILED"
    assert events[-1]["statusUpdate"]["final"] is True
    settled = client.settle_task_attempt.await_args.kwargs
    assert settled["status"] == "failed"
    assert settled["outcome_category"] == "execution"
    assert settled["failure_detail"] == "HTTPException"
    status_message = settled["status_message"]
    assert status_message["role"] == "ROLE_RESPONDER"
    assert status_message["parts"] == [{"text": "Task execution failed."}]
    assert status_message["metadata"][TASK_FAILURE_EXTENSION_URI] == {
        "code": "execution_failed",
        "category": "execution",
        "retryable": False,
        "attemptNumber": 1,
        "correlationId": "task-1",
    }
    client.list_task_messages.assert_not_awaited()


async def test_streaming_reports_terminalization_unknown_when_failure_cannot_be_settled():
    working = _task("working")
    client = _claimed_client(working)
    client.settle_task_attempt.side_effect = BackendError("backend unavailable")

    class Manager:
        settings = TauSDKSettings(_env_file=None)
        draining = False

        async def task_execution_fence(self, _session_uid):
            return SimpleNamespace(holder_id="holder-1", lease_token="lease-1")

        async def prompt(self, _context_id, _prompt, *, provenance=None, turn_uid=None):
            raise ValueError("tool failed")
            yield  # pragma: no cover

    with pytest.raises(TaskTerminalizationUnknownError) as captured:
        _events = [
            event
            async for event in _stream_task_events(
                client,
                Manager(),  # type: ignore[arg-type]
                working,
                prompt="Do it.",
                output_contract=_output_contract({}),
                max_output_bytes=1024,
                provenance={"channel": "a2a", "origin": "agent"},
            )
        ]

    assert captured.value.code == "task_terminalization_unknown"
    assert captured.value.detail == {
        "taskId": "task-1",
        "correlationId": "task-1",
        "retryable": True,
    }


async def test_task_return_wait_is_bounded_without_terminalizing_the_task():
    submitted = _task("submitted")
    client = AsyncMock()
    client.get_task_snapshot.return_value = AgentTaskSnapshot(task=submitted, event_cursor=1)

    result = await _wait_for_task_return_state(
        client,
        submitted,
        poll_interval_seconds=0.01,
        wait_timeout_seconds=0.02,
    )

    assert result.status == "submitted"
    assert client.get_task_snapshot.await_count >= 1
    client.settle_task_attempt.assert_not_awaited()


def test_legacy_status_detail_is_rejected_instead_of_manufacturing_a_message():
    interrupted = _task("input_required").model_copy(
        update={
            "status_message": {
                "message": "Choose an account.",
                "reason": "missing_account",
                "inputSchema": {"type": "string"},
            }
        }
    )

    with pytest.raises(ValueError, match="complete Main Sequence A2A Message"):
        _task_payload(interrupted)


async def test_stream_transport_reports_unknown_terminalization_without_raw_error(monkeypatch):
    async def failed_events(*_args, **_kwargs):
        raise ValueError("provider secret must not leave the process")
        yield  # pragma: no cover

    monkeypatch.setattr("ms_tau_sdk.api.a2a._stream_task_events", failed_events)

    class Manager:
        def mark_response_delivered(self, _session_uid):
            return False

    response = _message_stream_response(
        client=AsyncMock(),
        manager=Manager(),  # type: ignore[arg-type]
        task=_task("working"),
        prompt="Do it.",
        output_contract=_output_contract({}),
        max_output_bytes=1024,
        provenance={"channel": "a2a", "origin": "agent"},
    )
    body = b"".join([chunk async for chunk in response.body_iterator]).decode()
    payload = json.loads(body.removeprefix("data: ").strip())

    assert payload == {
        "error": {
            "code": "task_terminalization_unknown",
            "message": "Task execution stopped before its terminal state could be persisted",
            "detail": {
                "taskId": "task-1",
                "correlationId": "task-1",
                "retryable": True,
            },
        },
        "final": True,
    }
    assert "provider secret" not in body


async def test_subscription_replays_artifact_and_status_events_from_requested_cursor():
    submitted = _task()
    completed = _task("completed")
    client = AsyncMock()
    client.list_task_events.return_value = AgentTaskEventPage(
        events=[
            AgentTaskEvent(
                sequence=3,
                event_type="output_updated",
                payload={
                    "artifactId": "artifact-1",
                    "parts": [{"text": "done"}],
                    "append": True,
                    "lastChunk": True,
                },
            ),
            AgentTaskEvent(
                sequence=4,
                event_type="status_changed",
                status="completed",
            ),
        ],
        next_cursor=4,
    )
    client.get_task_snapshot.return_value = AgentTaskSnapshot(
        task=completed,
        event_cursor=4,
    )

    events = [
        event
        async for event in _task_subscription_events(
            client=client,
            snapshot=AgentTaskSnapshot(task=submitted, event_cursor=5),
            poll_interval_seconds=0.01,
            after_sequence=2,
        )
    ]

    assert [event["eventCursor"] for event in events] == [2, 3, 4]
    assert events[1]["artifactUpdate"]["artifact"] == {
        "artifactId": "artifact-1",
        "name": "",
        "parts": [{"text": "done"}],
        "metadata": {},
    }
    assert events[2]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_task_controls_bind_interruption_to_the_active_attempt():
    input_tool, authorization_tool = create_task_control_tools()
    input_context = TaskExecutionContext(
        task_uid="backend-task-1",
        task_id="task-1",
        context_id="session-1",
        attempt_uid="attempt-1",
        holder_id="holder-1",
        lease_token="lease-1",
    )
    with task_execution_scope(input_context):
        input_result = await input_tool.execute(
            "call-1",
            {
                "reason": "missing_account",
                "prompt": "Which account should be used?",
                "schema": {"type": "string"},
            },
        )

    assert input_result.details == {"accepted": True, "state": "input_required"}
    assert input_context.interruption_status == "input_required"
    assert input_context.interruption_message is not None
    assert input_context.interruption_message["role"] == "ROLE_RESPONDER"
    assert input_context.interruption_message["contextId"] == "session-1"
    assert input_context.interruption_message["taskId"] == "task-1"
    assert input_context.interruption_message["parts"] == [
        {"text": "Which account should be used?"}
    ]

    authorization_context = TaskExecutionContext(
        task_uid="backend-task-1",
        task_id="task-1",
        context_id="session-1",
        attempt_uid="attempt-2",
        holder_id="holder-1",
        lease_token="lease-1",
    )
    with task_execution_scope(authorization_context):
        authorization_result = await authorization_tool.execute(
            "call-2",
            {
                "requirement_reference": "provider-auth-7",
                "prompt": "Authorize the provider account.",
            },
        )

    assert authorization_result.details == {"accepted": True, "state": "auth_required"}
    assert authorization_context.interruption_status == "auth_required"
    assert authorization_context.interruption_message is not None
    assert authorization_context.interruption_message["role"] == "ROLE_RESPONDER"
    assert authorization_context.interruption_message["parts"] == [
        {"text": "Authorize the provider account."}
    ]


async def test_active_caller_turn_leaves_durable_delivery_queued():
    manager = SimpleNamespace(
        session_turn_active=lambda _session_uid: True,
        persist_platform_event=AsyncMock(),
    )

    with pytest.raises(HTTPException) as raised:
        await task_caller_delivery_available(
            {
                "delivery_uid": "delivery-1",
                "task_uid": "backend-task-1",
                "task_id": "task-1",
                "caller_agent_session_uid": "caller-session-1",
                "event_cursor": 8,
                "status": "completed",
            },
            manager,
        )

    assert raised.value.status_code == 409
    manager.persist_platform_event.assert_not_awaited()


async def test_caller_delivery_adds_bounded_platform_event_before_resuming():
    class Manager:
        def __init__(self) -> None:
            self.persisted_event = None
            self.background = None
            self.delivered: list[str] = []

        def session_turn_active(self, _session_uid):
            return False

        async def persist_platform_event(
            self,
            session_uid,
            platform_event,
            *,
            idempotency_key,
        ):
            self.persisted_event = (session_uid, platform_event, idempotency_key)
            return True

        def create_a2a_caller_delivery(self, delivery_uid, coroutine, *, name):
            assert delivery_uid == "delivery-1"
            assert name == "a2a-caller-delivery-delivery-1"
            self.background = coroutine
            return None, True

        async def prompt(
            self,
            _context_id,
            _prompt,
            *,
            provenance=None,
            platform_event=None,
        ):
            assert platform_event is None
            yield TauRuntimeEvent(type="agent_settled")

        def mark_response_delivered(self, session_uid: str) -> bool:
            self.delivered.append(session_uid)
            return True

    manager = Manager()
    result = await task_caller_delivery_available(
        {
            "delivery_uid": "delivery-1",
            "task_uid": "backend-task-1",
            "task_id": "task-1",
            "caller_agent_session_uid": "caller-session-1",
            "event_cursor": 8,
            "status": "completed",
        },
        manager,  # type: ignore[arg-type]
    )

    assert result == {
        "accepted": True,
        "event_persisted": True,
        "scheduled": True,
        "delivery_uid": "delivery-1",
    }
    assert manager.persisted_event == (
        "caller-session-1",
        (
            "io.mainsequence.a2a.task-delivery/v1",
            {
                "deliveryUid": "delivery-1",
                "taskUid": "backend-task-1",
                "taskId": "task-1",
                "state": "completed",
                "eventCursor": 8,
            },
        ),
        "delivery-1",
    )
    assert manager.background is not None
    await manager.background
    assert manager.delivered == ["caller-session-1"]


async def test_replayed_caller_delivery_does_not_schedule_a_second_continuation():
    manager = SimpleNamespace(
        session_turn_active=lambda _session_uid: False,
        persist_platform_event=AsyncMock(return_value=False),
        create_a2a_caller_delivery=Mock(),
    )

    result = await task_caller_delivery_available(
        {
            "delivery_uid": "delivery-1",
            "task_uid": "backend-task-1",
            "task_id": "task-1",
            "caller_agent_session_uid": "caller-session-1",
            "event_cursor": 8,
            "status": "completed",
        },
        manager,
    )

    assert result == {
        "accepted": True,
        "event_persisted": False,
        "scheduled": False,
        "delivery_uid": "delivery-1",
    }
    manager.create_a2a_caller_delivery.assert_not_called()
