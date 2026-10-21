"""Role translation between A2A v1, the Main Sequence Tau binding, and platform storage."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from urllib.parse import urlsplit


class A2AMessageDirection(StrEnum):
    REQUESTER = "ROLE_REQUESTER"
    RESPONDER = "ROLE_RESPONDER"


class A2AProtocolRole(StrEnum):
    """Roles defined by the public A2A v1 Message contract."""

    USER = "ROLE_USER"
    AGENT = "ROLE_AGENT"


class PlatformMessageRole(StrEnum):
    """Direction-neutral roles of a Task Message the platform stores."""

    USER = "user"
    AGENT = "agent"


_TO_BINDING = {
    A2AProtocolRole.USER.value: A2AMessageDirection.REQUESTER.value,
    A2AProtocolRole.AGENT.value: A2AMessageDirection.RESPONDER.value,
}
_TO_PROTOCOL = {value: key for key, value in _TO_BINDING.items()}
_FROM_PLATFORM = {
    PlatformMessageRole.USER.value: A2AMessageDirection.REQUESTER.value,
    PlatformMessageRole.AGENT.value: A2AMessageDirection.RESPONDER.value,
}


def _validate_message_shape(message: Mapping[str, object]) -> None:
    if not str(message.get("messageId") or "").strip():
        raise ValueError("A2A Message.messageId is required")
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("A2A Message.parts must be a non-empty array")
    extensions = message.get("extensions", [])
    if not isinstance(extensions, list) or any(
        not isinstance(item, str) or not item.strip() or not urlsplit(item).scheme
        for item in extensions
    ):
        raise ValueError("A2A Message.extensions must be an array of absolute URI strings")


def message_to_binding(message: Mapping[str, object]) -> dict[str, object]:
    """Translate only the role while preserving the complete Message content."""

    role = str(message.get("role") or "")
    if role not in _TO_BINDING:
        raise ValueError("A2A v1 Message role must be ROLE_USER or ROLE_AGENT")
    _validate_message_shape(message)
    return {**message, "role": _TO_BINDING[role]}


def message_to_protocol(message: Mapping[str, object]) -> dict[str, object]:
    """Translate a Main Sequence binding Message to its A2A v1 role."""

    role = str(message.get("role") or "")
    if role not in _TO_PROTOCOL:
        raise ValueError("Main Sequence Message role must be ROLE_REQUESTER or ROLE_RESPONDER")
    _validate_message_shape(message)
    return {**message, "role": _TO_PROTOCOL[role]}


def platform_message_to_binding(
    record: Mapping[str, object],
    *,
    context_id: str,
    task_id: str,
) -> dict[str, object]:
    """Translate a Task Message the platform stores into a Main Sequence binding Message.

    The platform stores the role ``user`` or ``agent`` and snake-case fields. The Task that owns
    the Message supplies its context and Task IDs. Fields only the platform keeps, such as the
    request hash and the requester, are left out.
    """

    role = str(record.get("role") or "")
    if role not in _FROM_PLATFORM:
        raise ValueError("Platform Task Message role must be user or agent")
    message: dict[str, object] = {
        "messageId": record.get("message_id"),
        "contextId": context_id,
        "taskId": task_id,
        "role": _FROM_PLATFORM[role],
        "parts": record.get("parts"),
        "metadata": record.get("metadata", {}),
        "extensions": record.get("extensions", []),
        "referenceTaskIds": record.get("reference_task_ids", []),
    }
    _validate_message_shape(message)
    return message


def has_a2a_direction(
    message: Mapping[str, object],
    direction: A2AMessageDirection,
) -> bool:
    """Check the actual Main Sequence A2A wire role."""

    return message.get("role") == direction.value


__all__ = [
    "A2AMessageDirection",
    "A2AProtocolRole",
    "PlatformMessageRole",
    "has_a2a_direction",
    "message_to_binding",
    "message_to_protocol",
    "platform_message_to_binding",
]
