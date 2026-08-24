"""Content-free lifecycle instrumentation for translated Tau events."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

import structlog

from astro.logging import safe_log

from .events import AstroRuntimeEvent


def _bounded(value: object, *, limit: int = 128) -> str | None:
    normalized = str(value or "").strip()
    return normalized[:limit] if normalized else None


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = mapping.get(name)
        if value is not None:
            return value
    return None


def _nested_mapping(value: object, *names: str) -> Mapping[str, Any]:
    current = value
    for name in names:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(name)
    return current if isinstance(current, Mapping) else {}


def _usage(data: Mapping[str, Any]) -> dict[str, int]:
    candidates = [
        data,
        _nested_mapping(data, "usage"),
        _nested_mapping(data, "message", "usage"),
        _nested_mapping(data, "response", "usage"),
    ]
    aliases = {
        "input_tokens": ("input_tokens", "input", "prompt_tokens"),
        "output_tokens": ("output_tokens", "output", "completion_tokens"),
        "cached_input_tokens": ("cached_input_tokens", "cache_read_tokens"),
        "reasoning_tokens": ("reasoning_tokens", "thinking_tokens"),
        "total_tokens": ("total_tokens", "total"),
    }
    fields: dict[str, int] = {}
    for candidate in candidates:
        for field, names in aliases.items():
            value = _first(candidate, *names)
            if isinstance(value, int | float) and field not in fields:
                fields[field] = int(value)
    return fields


def _result_size(data: Mapping[str, Any]) -> int | None:
    result = _first(data, "result", "output")
    if result is None:
        return None
    try:
        return len(json.dumps(result, default=str, separators=(",", ":")).encode())
    except (TypeError, ValueError, OverflowError):
        return None


class TauTurnObserver:
    """Translate Tau's event stream into canonical safe lifecycle events."""

    def __init__(self, *, provider: str, model: str) -> None:
        self.provider = _bounded(provider) or "unknown"
        self.model = _bounded(model) or "unknown"
        self.logger = structlog.get_logger("astro.agent_lifecycle")
        self.started_at = time.monotonic()
        self.first_token_ms: float | None = None
        self.model_calls = 0
        self.tool_calls = 0
        self.handoffs = 0
        self.usage: dict[str, int] = {}
        self._model: tuple[str, float, int] | None = None
        self._next_model_attempt = 1
        self._tools: dict[str, tuple[float, str, str]] = {}
        self._handoffs: dict[
            str,
            tuple[float, str, str, str | None, str | None, str],
        ] = {}

    def observe(self, event: AstroRuntimeEvent) -> None:
        event_type = event.type.lower()
        data = event.data

        if event_type in {"message_start", "model_start", "model_request_start"}:
            self._start_model()
        elif event_type in {"message_end", "model_end", "model_response_end"}:
            self._finish_model(data)
        elif event_type in {
            "message_error",
            "model_error",
            "model_failed",
            "model_rate_limited",
            "rate_limited",
            "response_error",
        }:
            self._fail_model(data, event_type=event_type)

        if event_type in {"text_delta", "message_delta"} and self.first_token_ms is None:
            self.first_token_ms = round(
                (time.monotonic() - self.started_at) * 1000,
                3,
            )

        if event_type in {"tool_execution_start", "tool_start"}:
            self._start_tool(data)
        elif event_type in {"tool_execution_end", "tool_end"}:
            self._finish_tool(data)

        if "handoff" in event_type or "subagent" in event_type:
            if event_type.endswith(("start", "started")):
                self._start_handoff(data)
            elif event_type.endswith(("end", "completed", "failed")):
                self._finish_handoff(data, failed=event_type.endswith("failed"))

        for field, value in _usage(data).items():
            self.usage[field] = max(self.usage.get(field, 0), value)

    def _start_model(self) -> None:
        if self._model is not None:
            return
        call_uid = str(uuid.uuid4())
        attempt = self._next_model_attempt
        self._model = (call_uid, time.monotonic(), attempt)
        self.model_calls += 1
        safe_log(
            self.logger,
            "info",
            "agent.model.started",
            message="Model call started",
            model_call_uid=call_uid,
            model_provider=self.provider,
            model_name=self.model,
            model_operation="response",
            model_attempt=attempt,
        )

    def _finish_model(self, data: Mapping[str, Any]) -> None:
        if self._model is None:
            self._start_model()
        call_uid, started, attempt = self._model or (
            str(uuid.uuid4()),
            time.monotonic(),
            self._next_model_attempt,
        )
        usage = _usage(data)
        self.usage.update(usage)
        safe_log(
            self.logger,
            "info",
            "agent.model.completed",
            message="Model call completed",
            model_call_uid=call_uid,
            model_provider=self.provider,
            model_name=self.model,
            model_operation="response",
            model_attempt=attempt,
            model_duration_ms=round((time.monotonic() - started) * 1000, 3),
            model_first_token_ms=self.first_token_ms,
            finish_reason=_bounded(_first(data, "stopReason", "stop_reason", "finish_reason")),
            provider_request_id=_bounded(_first(data, "provider_request_id", "response_id")),
            rate_limited=False,
            outcome="success",
            **usage,
        )
        self._model = None
        self._next_model_attempt = 1

    def _fail_model(self, data: Mapping[str, Any], *, event_type: str) -> None:
        if self._model is None:
            self._start_model()
        call_uid, started, attempt = self._model or (
            str(uuid.uuid4()),
            time.monotonic(),
            self._next_model_attempt,
        )
        error_type = _bounded(_first(data, "error_type", "errorType")) or "ModelError"
        raw_status_code = _first(data, "status_code", "status", "http_status")
        status_code = raw_status_code if isinstance(raw_status_code, int) else None
        rate_limited = (
            status_code == 429
            or "rate_limit" in event_type
            or "ratelimit" in error_type.lower()
        )
        retryable = rate_limited or status_code in {408, 409, 425, 500, 502, 503, 504}
        safe_log(
            self.logger,
            "warning" if retryable else "error",
            "agent.model.failed",
            message="Model call failed",
            model_call_uid=call_uid,
            model_provider=self.provider,
            model_name=self.model,
            model_operation="response",
            model_attempt=attempt,
            model_duration_ms=round((time.monotonic() - started) * 1000, 3),
            model_error_type=error_type,
            provider_request_id=_bounded(_first(data, "provider_request_id", "response_id")),
            rate_limited=rate_limited,
            retryable=retryable,
            outcome="rate_limited" if rate_limited else "failed",
        )
        self._model = None
        self._next_model_attempt = attempt + 1 if retryable else 1

    def _tool_identity(self, data: Mapping[str, Any]) -> tuple[str, str, str]:
        call_uid = _bounded(_first(data, "toolCallId", "tool_call_id", "id")) or str(uuid.uuid4())
        name = _bounded(_first(data, "toolName", "tool_name", "name")) or "unknown"
        category = _bounded(_first(data, "toolCategory", "tool_category")) or "other"
        return call_uid, name, category

    def _start_tool(self, data: Mapping[str, Any]) -> None:
        call_uid, name, category = self._tool_identity(data)
        self._tools[call_uid] = (time.monotonic(), name, category)
        self.tool_calls += 1
        safe_log(
            self.logger,
            "info",
            "agent.tool.started",
            message="Tool call started",
            tool_call_uid=call_uid,
            tool_name=name,
            tool_category=category,
            side_effect_class=_bounded(data.get("side_effect_class")) or "unknown",
            approval_required=bool(data.get("approval_required", False)),
            tool_attempt=1,
        )

    def _finish_tool(self, data: Mapping[str, Any]) -> None:
        call_uid, fallback_name, fallback_category = self._tool_identity(data)
        started, name, category = self._tools.pop(
            call_uid,
            (time.monotonic(), fallback_name, fallback_category),
        )
        error_type = _bounded(_first(data, "errorType", "error_type"))
        outcome = "failed" if error_type else "success"
        safe_log(
            self.logger,
            "error" if error_type else "info",
            "agent.tool.failed" if error_type else "agent.tool.completed",
            message="Tool call completed" if not error_type else "Tool call failed",
            tool_call_uid=call_uid,
            tool_name=name,
            tool_category=category,
            result_size_bytes=_result_size(data),
            tool_error_type=error_type,
            tool_outcome=outcome,
            outcome=outcome,
            duration_ms=round((time.monotonic() - started) * 1000, 3),
        )

    def _start_handoff(self, data: Mapping[str, Any]) -> None:
        handoff_uid = _bounded(data.get("handoff_uid")) or str(uuid.uuid4())
        source = _bounded(_first(data, "source_agent_uid", "source")) or "tau"
        target = _bounded(_first(data, "target_agent_uid", "target")) or "subagent"
        parent_session = _bounded(
            _first(data, "parent_agent_session_uid", "parent_session_uid")
        )
        child_session = _bounded(_first(data, "child_agent_session_uid", "child_session_uid"))
        requested_reason = _bounded(_first(data, "handoff_reason_code", "reason_code"))
        handoff_reason = (
            requested_reason
            if requested_reason in {"delegation", "specialist", "supervisor", "retry"}
            else "tau_subagent_event"
        )
        self._handoffs[handoff_uid] = (
            time.monotonic(),
            source,
            target,
            parent_session,
            child_session,
            handoff_reason,
        )
        self.handoffs += 1
        safe_log(
            self.logger,
            "info",
            "agent.handoff.started",
            message="Agent handoff started",
            handoff_uid=handoff_uid,
            source_agent_uid=source,
            target_agent_uid=target,
            parent_agent_session_uid=parent_session,
            child_agent_session_uid=child_session,
            handoff_reason_code=handoff_reason,
        )

    def _finish_handoff(self, data: Mapping[str, Any], *, failed: bool) -> None:
        handoff_uid = _bounded(data.get("handoff_uid"))
        if handoff_uid is None:
            source = _bounded(_first(data, "source_agent_uid", "source")) or "tau"
            target = _bounded(_first(data, "target_agent_uid", "target")) or "subagent"
            handoff_uid = next(
                (
                    uid
                    for uid, (
                        _started,
                        candidate_source,
                        candidate_target,
                        _parent_session,
                        _child_session,
                        _reason,
                    ) in reversed(
                        self._handoffs.items()
                    )
                    if candidate_source == source and candidate_target == target
                ),
                None,
            )
        if handoff_uid is None or handoff_uid not in self._handoffs:
            self._start_handoff(data)
            handoff_uid = next(reversed(self._handoffs))
        started, source, target, parent_session, child_session, handoff_reason = (
            self._handoffs.pop(handoff_uid)
        )
        safe_log(
            self.logger,
            "warning" if failed else "info",
            "agent.handoff.completed" if not failed else "agent.handoff.failed",
            message="Agent handoff completed" if not failed else "Agent handoff failed",
            handoff_uid=handoff_uid,
            source_agent_uid=source,
            target_agent_uid=target,
            parent_agent_session_uid=parent_session,
            child_agent_session_uid=child_session,
            handoff_reason_code=handoff_reason,
            handoff_outcome="failed" if failed else "completed",
            outcome="failed" if failed else "success",
            duration_ms=round((time.monotonic() - started) * 1000, 3),
        )

    def terminal_fields(self) -> dict[str, object]:
        return {
            "execution_duration_ms": round(
                (time.monotonic() - self.started_at) * 1000,
                3,
            ),
            "first_token_ms": self.first_token_ms,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "handoffs": self.handoffs,
            **self.usage,
        }


__all__ = ["TauTurnObserver"]
