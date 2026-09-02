"""Generic terminal assistant-failure extraction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TerminalAssistantFailure:
    """Provider-neutral failure carried by a terminal Tau assistant message."""

    reason: str
    message: str
    error_type: str
    status_code: int | None = None


def terminal_assistant_failure(
    data: Mapping[str, Any],
) -> TerminalAssistantFailure | None:
    """Extract a terminal Tau assistant failure without provider-specific mapping."""
    message = data.get("message")
    if not isinstance(message, Mapping) or message.get("role") != "assistant":
        return None

    reason_value = message.get("stopReason", message.get("stop_reason"))
    reason = str(reason_value or "")
    if reason not in {"error", "aborted"}:
        return None

    error_message = _nonempty_text(message.get("errorMessage", message.get("error_message")))
    if error_message is None:
        error_message = "Agent turn was aborted" if reason == "aborted" else "Provider error"

    status_code = None
    provider_error = False
    diagnostics = message.get("diagnostics")
    if isinstance(diagnostics, list):
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, Mapping) or diagnostic.get("type") != "provider_error":
                continue
            provider_error = True
            details = diagnostic.get("details")
            if isinstance(details, Mapping):
                candidate = details.get("status_code")
                if isinstance(candidate, int) and not isinstance(candidate, bool):
                    status_code = candidate
            break

    return TerminalAssistantFailure(
        reason=reason,
        message=error_message,
        error_type=(
            "ProviderError"
            if provider_error
            else "AssistantAborted"
            if reason == "aborted"
            else "AssistantError"
        ),
        status_code=status_code,
    )


def is_terminal_assistant_message(data: Mapping[str, Any]) -> bool:
    """Return whether the event data contains a completed assistant message."""
    message = data.get("message")
    if not isinstance(message, Mapping) or message.get("role") != "assistant":
        return False
    reason = message.get("stopReason", message.get("stop_reason"))
    return isinstance(reason, str) and bool(reason)


def _nonempty_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


__all__ = [
    "TerminalAssistantFailure",
    "is_terminal_assistant_message",
    "terminal_assistant_failure",
]
