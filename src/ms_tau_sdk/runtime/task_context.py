"""Turn-local A2A Task identity and structured interruption request."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

TaskInterruptionStatus = Literal["input_required", "auth_required"]


@dataclass(slots=True)
class TaskExecutionContext:
    task_uid: str
    task_id: str
    attempt_uid: str
    holder_id: str
    lease_token: str
    interruption_status: TaskInterruptionStatus | None = None
    interruption_message: dict[str, Any] | None = None

    def request_interruption(
        self,
        *,
        status: TaskInterruptionStatus,
        message: dict[str, Any],
    ) -> None:
        if self.interruption_status is not None:
            raise ValueError("This Task attempt already requested an interruption.")
        self.interruption_status = status
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
