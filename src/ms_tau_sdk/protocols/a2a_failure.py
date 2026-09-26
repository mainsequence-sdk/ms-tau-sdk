"""Safe, standard A2A status Messages for Task failures and interruptions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ms_tau_sdk.errors import BackendConflictError
from ms_tau_sdk.protocols.a2a_message import agent_message
from ms_tau_sdk.protocols.a2a_roles import A2AMessageDirection, message_to_protocol

TASK_FAILURE_EXTENSION_URI = "https://mainsequence.ai/a2a/extensions/task-failure/v1"
TASK_STATUS_DETAIL_EXTENSION_URI = "https://mainsequence.ai/a2a/extensions/task-status-detail/v1"


def _is_a2a_message(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and str(value.get("messageId") or "").strip()
        and value.get("role")
        in {
            A2AMessageDirection.REQUESTER.value,
            A2AMessageDirection.RESPONDER.value,
        }
        and isinstance(value.get("parts"), list)
        and value["parts"]
    )


def failure_status_message(
    *,
    context_id: str,
    task_id: str,
    failure_code: str,
    category: str,
    retryable: bool,
    attempt_number: int,
    correlation_id: str,
    text: str = "Task execution failed.",
) -> dict[str, Any]:
    """Build a bounded public failure Message without exception or payload contents."""

    message = agent_message(
        context_id=context_id,
        text=text,
        strict_json=False,
        metadata={
            TASK_FAILURE_EXTENSION_URI: {
                "code": failure_code,
                "category": category,
                "retryable": retryable,
                "attemptNumber": attempt_number,
                "correlationId": correlation_id,
            }
        },
    )
    message["taskId"] = task_id
    message["extensions"] = [TASK_FAILURE_EXTENSION_URI]
    return message


def normalize_status_message(
    value: object,
    *,
    context_id: str,
    task_id: str,
) -> dict[str, Any] | None:
    """Return an already durable status Message without manufacturing identity."""

    if not value:
        return None
    if not _is_a2a_message(value) or not isinstance(value, Mapping):
        raise ValueError("Stored status_message is not a complete Main Sequence A2A Message")
    message = {str(key): item for key, item in value.items()}
    if message.get("role") != A2AMessageDirection.RESPONDER.value:
        raise ValueError("Stored status_message must use ROLE_RESPONDER")
    if message.get("contextId") != context_id or message.get("taskId") != task_id:
        raise ValueError("Stored status_message does not belong to the Task")
    message_to_protocol(message)
    return message


def validate_status_message(
    value: Mapping[str, Any] | None,
    *,
    context_id: str,
    task_id: str,
) -> dict[str, Any] | None:
    """Validate the hard-cut settlement Message contract."""

    if value is None:
        return None
    try:
        return normalize_status_message(value, context_id=context_id, task_id=task_id)
    except ValueError as error:
        raise BackendConflictError(str(error)) from error


def failure_details(value: object) -> dict[str, Any]:
    """Read the safe public failure extension from a stored status Message."""

    if not isinstance(value, Mapping):
        return {}
    metadata = value.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    details = metadata.get(TASK_FAILURE_EXTENSION_URI)
    return dict(details) if isinstance(details, Mapping) else {}


__all__ = [
    "TASK_FAILURE_EXTENSION_URI",
    "TASK_STATUS_DETAIL_EXTENSION_URI",
    "failure_details",
    "failure_status_message",
    "normalize_status_message",
    "validate_status_message",
]
