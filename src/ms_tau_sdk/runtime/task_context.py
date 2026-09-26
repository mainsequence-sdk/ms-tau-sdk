"""Turn-local A2A Task identity and structured interruption request."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from ms_tau_sdk.protocols.a2a_failure import TASK_STATUS_DETAIL_EXTENSION_URI
from ms_tau_sdk.protocols.a2a_message import agent_message

TaskInterruptionStatus = Literal["input_required", "auth_required"]


@dataclass(slots=True)
class TaskExecutionContext:
    task_uid: str
    task_id: str
    context_id: str
    attempt_uid: str
    holder_id: str
    lease_token: str
    interruption_status: TaskInterruptionStatus | None = None
    interruption_message: dict[str, Any] | None = None

    def request_interruption(
        self,
        *,
        status: TaskInterruptionStatus,
        text: str,
        details: dict[str, Any],
    ) -> None:
        if self.interruption_status is not None:
            raise ValueError("This Task attempt already requested an interruption.")
        self.interruption_status = status
        message = agent_message(
            context_id=self.context_id,
            text=text,
            strict_json=False,
            metadata={TASK_STATUS_DETAIL_EXTENSION_URI: details},
        )
        message["taskId"] = self.task_id
        message["extensions"] = [TASK_STATUS_DETAIL_EXTENSION_URI]
        self.interruption_message = message


_ACTIVE_TASK: ContextVar[TaskExecutionContext | None] = ContextVar(
    "ms_tau_sdk_active_a2a_task",
    default=None,
)


def active_task_execution() -> TaskExecutionContext | None:
    return _ACTIVE_TASK.get()


@contextmanager
def task_execution_scope(context: TaskExecutionContext) -> Iterator[TaskExecutionContext]:
    token = _ACTIVE_TASK.set(context)
    try:
        yield context
    finally:
        _ACTIVE_TASK.reset(token)
