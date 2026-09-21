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

    provider_error, status_code = _provider_error(message.get("diagnostics"))

    error_message = _nonempty_text(message.get("errorMessage", message.get("error_message")))
    if error_message is None:
        # Tau can end a turn with ``stopReason: "error"`` and no message at all.
        # Returning the bare constant would discard the only diagnostic left, so
        # the status code is composed into the message the caller sees.
        error_message = _composed_message(reason=reason, status_code=status_code)

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


def _provider_error(diagnostics: object) -> tuple[bool, int | None]:
    """Return whether a provider error was diagnosed, and its status code.

    Only the status code is read out of ``details``; the raw provider response
    body that sits beside it never leaves this function.
    """
    if not isinstance(diagnostics, list):
        return False, None
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, Mapping) or diagnostic.get("type") != "provider_error":
            continue
        details = diagnostic.get("details")
        if isinstance(details, Mapping):
            candidate = details.get("status_code")
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                return True, candidate
        return True, None
    return False, None


def _composed_message(*, reason: str, status_code: int | None) -> str:
    fallback = "Agent turn was aborted" if reason == "aborted" else "Provider error"
    if status_code is None:
        return fallback
    return f"{fallback} (HTTP {status_code})"


def _nonempty_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


__all__ = [
    "TerminalAssistantFailure",
    "is_terminal_assistant_message",
    "terminal_assistant_failure",
]
