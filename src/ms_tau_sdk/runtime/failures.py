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
    provider_error_type: str | None = None
    transport_phase: str | None = None
    attempts: int | None = None
    retry_exhausted: bool | None = None
    provider_duration_ms: float | None = None
    diagnostic_source: str | None = None
    failure_uid: str | None = None
    safe_summary: str | None = None


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

    provider_error = _provider_error(message.get("diagnostics"))
    status_code = provider_error.status_code if provider_error is not None else None

    error_message = _nonempty_text(message.get("errorMessage", message.get("error_message")))
    if error_message is None:
        # Tau can end a turn with ``stopReason: "error"`` and no message at all.
        # Returning the bare constant would discard the only diagnostic left, so
        # the status code is composed into the message the caller sees.
        error_message = _composed_message(
            reason=reason,
            status_code=status_code,
            provider_error_type=(provider_error.error_type if provider_error is not None else None),
            attempts=provider_error.attempts if provider_error is not None else None,
        )

    return TerminalAssistantFailure(
        reason=reason,
        message=error_message,
        error_type=(
            "ProviderError"
            if provider_error is not None
            else "AssistantAborted"
            if reason == "aborted"
            else "AssistantError"
        ),
        status_code=status_code,
        provider_error_type=(provider_error.error_type if provider_error is not None else None),
        transport_phase=(provider_error.transport_phase if provider_error is not None else None),
        attempts=provider_error.attempts if provider_error is not None else None,
        retry_exhausted=(provider_error.retry_exhausted if provider_error is not None else None),
        provider_duration_ms=(
            provider_error.provider_duration_ms if provider_error is not None else None
        ),
        diagnostic_source=(
            provider_error.diagnostic_source if provider_error is not None else None
        ),
        failure_uid=provider_error.failure_uid if provider_error is not None else None,
        safe_summary=_composed_message(
            reason=reason,
            status_code=status_code,
            provider_error_type=(provider_error.error_type if provider_error is not None else None),
            attempts=provider_error.attempts if provider_error is not None else None,
        ),
    )


def is_terminal_assistant_message(data: Mapping[str, Any]) -> bool:
    """Return whether the event data contains a completed assistant message."""
    message = data.get("message")
    if not isinstance(message, Mapping) or message.get("role") != "assistant":
        return False
    reason = message.get("stopReason", message.get("stop_reason"))
    return isinstance(reason, str) and bool(reason)


@dataclass(frozen=True, slots=True)
class _ProviderErrorDiagnostic:
    status_code: int | None = None
    error_type: str | None = None
    transport_phase: str | None = None
    attempts: int | None = None
    retry_exhausted: bool | None = None
    provider_duration_ms: float | None = None
    diagnostic_source: str | None = None
    failure_uid: str | None = None


def _provider_error(diagnostics: object) -> _ProviderErrorDiagnostic | None:
    """Return the bounded, non-payload portion of a provider diagnostic.

    The raw provider response body that sits beside these fields never leaves
    this function.
    """
    if not isinstance(diagnostics, list):
        return None
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, Mapping) or diagnostic.get("type") != "provider_error":
            continue
        details = diagnostic.get("details")
        if not isinstance(details, Mapping):
            return _ProviderErrorDiagnostic()
        status_value = details.get("status_code")
        attempts_value = details.get("attempts")
        duration_value = details.get("provider_duration_ms")
        retry_exhausted = details.get("retry_exhausted")
        return _ProviderErrorDiagnostic(
            status_code=(
                status_value
                if isinstance(status_value, int) and not isinstance(status_value, bool)
                else None
            ),
            error_type=_bounded_text(details.get("error_type")),
            transport_phase=_bounded_text(details.get("transport_phase")),
            attempts=(
                attempts_value
                if isinstance(attempts_value, int)
                and not isinstance(attempts_value, bool)
                and attempts_value > 0
                else None
            ),
            retry_exhausted=retry_exhausted if isinstance(retry_exhausted, bool) else None,
            provider_duration_ms=(
                float(duration_value)
                if isinstance(duration_value, int | float)
                and not isinstance(duration_value, bool)
                and duration_value >= 0
                else None
            ),
            diagnostic_source=_bounded_text(details.get("diagnostic_source")),
            failure_uid=_bounded_text(details.get("failure_uid")),
        )
    return None


def _composed_message(
    *,
    reason: str,
    status_code: int | None,
    provider_error_type: str | None,
    attempts: int | None,
) -> str:
    fallback = "Agent turn was aborted" if reason == "aborted" else "Provider error"
    if provider_error_type is not None:
        if attempts is None:
            return provider_error_type
        suffix = "attempt" if attempts == 1 else "attempts"
        return f"{provider_error_type} after {attempts} {suffix}"
    if status_code is None:
        return fallback
    return f"{fallback} (HTTP {status_code})"


def _nonempty_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _bounded_text(value: object, *, limit: int = 128) -> str | None:
    text = _nonempty_text(value)
    return text[:limit] if text is not None else None


__all__ = [
    "TerminalAssistantFailure",
    "is_terminal_assistant_message",
    "terminal_assistant_failure",
]
