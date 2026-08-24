"""Structlog configuration aligned with the Main Sequence Django backend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from collections.abc import Mapping
from typing import Any, cast

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import (
    bind_contextvars,
    clear_contextvars,
    get_contextvars,
)
from structlog.typing import EventDict, Processor, WrappedLogger

SENSITIVE_EXACT_KEYS = {
    "api_key",
    "authorization",
    "client_ip",
    "client_secret",
    "content",
    "contents",
    "cookie",
    "credential",
    "credentials",
    "error_message",
    "messages",
    "password",
    "prompt",
    "prompt_excerpt",
    "prompt_hash",
    "prompt_text",
    "proxy_authorization",
    "remote_ip",
    "request_body",
    "response_body",
    "secret",
    "set_cookie",
    "system_prompt",
    "token",
    "tool_args",
    "tool_arguments",
    "tool_input",
    "tool_output",
    "tool_result",
    "user_agent",
}
BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+(?!bearer\b)[A-Za-z0-9._~+/=-]+")
CALLSITE_PARAMETERS = {
    structlog.processors.CallsiteParameter.PATHNAME,
    structlog.processors.CallsiteParameter.FILENAME,
    structlog.processors.CallsiteParameter.MODULE,
    structlog.processors.CallsiteParameter.LINENO,
    structlog.processors.CallsiteParameter.FUNC_NAME,
}
MAX_CORRELATION_LENGTH = 128
CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
TRACEPARENT_PATTERN = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
RUNTIME_INSTANCE_UID = str(uuid.uuid4())
PROCESS_STARTED_AT = time.monotonic()
PLATFORM_PROBE_PATHS = frozenset({"/health", "/ready"})
PROBE_FAILURE_LOG_INTERVAL_SECONDS = 60.0
PLATFORM_EVENT_PREFIXES = (
    "http.request.",
    "runtime.",
    "agent.",
    "dependency.call.",
)

ENVIRONMENT_CONTEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "organization_uid": ("MAINSEQUENCE_ORGANIZATION_UID", "ORGANIZATION_UID"),
    "project_uid": ("MAINSEQUENCE_PROJECT_UID", "PROJECT_UID"),
    "organization_project_environment_uid": ("MAINSEQUENCE_ORGANIZATION_PROJECT_ENVIRONMENT_UID",),
    "coding_agent_service_uid": (
        "MAINSEQUENCE_CODING_AGENT_SERVICE_UID",
        "CODING_AGENT_SERVICE_UID",
    ),
    "deployment_run_uid": (
        "MAINSEQUENCE_DEPLOYMENT_RUN_UID",
        "DEPLOYMENT_RUN_UID",
    ),
    "runtime_revision": ("K_REVISION", "RUNTIME_REVISION"),
    "code_revision": ("SOURCE_COMMIT_SHA", "CODE_REVISION"),
    "image_uid": ("MAINSEQUENCE_IMAGE_UID", "IMAGE_UID"),
    "image_digest": ("MAINSEQUENCE_IMAGE_DIGEST", "IMAGE_DIGEST"),
    "agent_config_revision": ("AGENT_CONFIG_REVISION",),
    "toolset_revision": ("TOOLSET_REVISION",),
    "model_config_revision": ("MODEL_CONFIG_REVISION",),
}


def conversation_log_fields(
    prompt: str,
    *,
    include_excerpt: bool,
    message_count: int | None = None,
) -> dict[str, object]:
    """Build safe-by-default metadata for a validated conversation prompt."""

    del include_excerpt
    fields: dict[str, object] = {"input_size_bytes": len(prompt.encode("utf-8"))}
    if message_count is not None:
        fields["message_count"] = message_count
    return fields


def _sanitize(value: object, *, key: str = "") -> object:
    normalized_key = key.lower().replace("-", "_")
    if normalized_key in SENSITIVE_EXACT_KEYS or normalized_key.endswith(
        ("_credential", "_password", "_secret", "_token")
    ):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(child_key): _sanitize(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [_sanitize(child) for child in value]
    if isinstance(value, str):
        return BEARER_PATTERN.sub("Bearer [REDACTED]", value)
    return value


def safe_log(
    bound_logger: Any,
    method: str,
    event: str,
    **fields: object,
) -> None:
    """Keep a telemetry failure from changing request or agent behavior."""

    try:
        getattr(bound_logger, method)(event, **fields)
    except Exception:  # pragma: no cover - verified with an intentionally failing logger
        return


def _bind_context(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    context = get_contextvars()
    for key, value in context.items():
        event_dict.setdefault(key, value)
    event_dict.setdefault("request_id", context.get("request_id"))
    return event_dict


def _add_google_cloud_trace_fields(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    trace_id = event_dict.pop("otelTraceID", None)
    span_id = event_dict.pop("otelSpanID", None)
    sampled = event_dict.pop("otelTraceSampled", False)
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project_id and trace_id and trace_id != "0" and span_id and span_id != "0":
        event_dict["logging.googleapis.com/trace"] = f"projects/{project_id}/traces/{trace_id}"
        event_dict["logging.googleapis.com/spanId"] = span_id
        event_dict["logging.googleapis.com/trace_sampled"] = sampled
    return event_dict


def _add_source(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    pathname = event_dict.get("pathname") or event_dict.get("filename")
    lineno = event_dict.get("lineno")
    if pathname and lineno:
        event_dict["source"] = f"{pathname}:{lineno}"
    return event_dict


def _add_severity(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    level = str(event_dict.get("level") or "").upper()
    if level:
        event_dict["severity"] = level
    return event_dict


def _add_component(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    event_dict.setdefault("component", event_dict.get("logger") or "astro")
    return event_dict


def _remove_platform_callsite(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    event = str(event_dict.get("event") or "")
    if event.startswith(PLATFORM_EVENT_PREFIXES):
        for key in (
            "pathname",
            "filename",
            "module",
            "lineno",
            "func_name",
            "source",
        ):
            event_dict.pop(key, None)
    return event_dict


def _environment_context() -> dict[str, object]:
    fields: dict[str, object] = {
        "runtime_instance_uid": RUNTIME_INSTANCE_UID,
        "sdk_version": "mainsequence-astro/4",
    }
    for field, names in ENVIRONMENT_CONTEXT_FIELDS.items():
        for name in names:
            value = str(os.getenv(name) or "").strip()
            if value:
                fields[field] = value[:MAX_CORRELATION_LENGTH]
                break
    return fields


def _add_event_envelope(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    event_dict.pop("project_environment_uid", None)
    event_dict.setdefault("event_id", str(uuid.uuid4()))
    event_dict.setdefault("message", str(event_dict.get("event") or ""))
    environment_context = _environment_context()
    for key, value in environment_context.items():
        if key != "organization_project_environment_uid":
            event_dict.setdefault(key, value)
    trusted_environment_uid = environment_context.get(
        "organization_project_environment_uid"
    ) or get_contextvars().get(
        "organization_project_environment_uid",
    )
    if trusted_environment_uid is not None:
        event_dict["organization_project_environment_uid"] = trusted_environment_uid
    return event_dict


def _sanitize_event(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    return {str(key): _sanitize(value, key=str(key)) for key, value in event_dict.items()}


class ClickableConsoleRenderer(structlog.dev.ConsoleRenderer):
    """Render readable one-line logs without dropping structured fields."""

    def __call__(
        self,
        logger: WrappedLogger,
        method_name: str,
        event_dict: EventDict,
    ) -> str:
        console_event = dict(event_dict)
        message = console_event.pop("message", None)
        if isinstance(message, str) and message:
            console_event["event"] = message

        # ConsoleRenderer already renders logger and level prominently.
        console_event.pop("severity", None)
        console_event.pop("component", None)
        console_event.pop("source", None)
        return super().__call__(logger, method_name, console_event)


def _common_processors() -> list[Processor]:
    return [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _bind_context,
        _add_google_cloud_trace_fields,
        structlog.dev.set_exc_info,
        structlog.processors.format_exc_info,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.CallsiteParameterAdder(CALLSITE_PARAMETERS),
        _add_source,
        _add_severity,
        _add_component,
        _remove_platform_callsite,
        _add_event_envelope,
        _sanitize_event,
    ]


def _handler(
    *,
    stream: Any,
    renderer: Processor,
    foreign_pre_chain: list[Processor],
) -> logging.Handler:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
            foreign_pre_chain=foreign_pre_chain,
        )
    )
    return handler


def configure_logging(
    level: str,
    *,
    machine_sink: bool = True,
    human_sink: bool = False,
) -> None:
    """Configure native Structlog and foreign stdlib logs through one contract."""

    clear_contextvars()
    processors = _common_processors()
    structlog.configure(
        processors=[
            *processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    handlers: list[logging.Handler] = []
    if machine_sink:
        handlers.append(
            _handler(
                stream=sys.stdout,
                renderer=structlog.processors.JSONRenderer(
                    serializer=json.dumps,
                    default=str,
                    separators=(",", ":"),
                ),
                foreign_pre_chain=processors,
            )
        )
    if human_sink:
        handlers.append(
            _handler(
                stream=sys.stderr,
                renderer=ClickableConsoleRenderer(
                    colors=sys.stderr.isatty(),
                    exception_formatter=structlog.dev.plain_traceback,
                ),
                foreign_pre_chain=processors,
            )
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    for handler in handlers:
        root.addHandler(handler)

    logging.captureWarnings(True)
    logging.getLogger("uvicorn.access").disabled = True


def _request_field(scope: Scope, name: bytes) -> str | None:
    for header_name, value in scope.get("headers", ()):
        if header_name.lower() == name:
            return cast(bytes, value).decode("latin-1")
    return None


def _bounded_identifier(value: object) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > MAX_CORRELATION_LENGTH:
        return None
    return normalized if CORRELATION_PATTERN.fullmatch(normalized) else None


def _request_id(scope: Scope) -> str:
    return _bounded_identifier(_request_field(scope, b"x-request-id")) or str(uuid.uuid4())


def _trace_context(scope: Scope) -> tuple[str, str | None, bool]:
    raw = str(_request_field(scope, b"traceparent") or "").strip().lower()
    match = TRACEPARENT_PATTERN.fullmatch(raw)
    if match and match.group(1) != "0" * 32 and match.group(2) != "0" * 16:
        return match.group(1), match.group(2), bool(int(match.group(3), 16) & 1)
    return uuid.uuid4().hex, None, False


def _route_path(scope: Scope) -> str:
    route = scope.get("route")
    route_path = getattr(route, "path", None)
    return str(route_path or "__unmatched__")


def bind_request_log_fields(scope: Scope, **fields: object) -> None:
    """Add safe request-local fields to terminal and nested application logs."""

    state = scope.setdefault("state", {})
    request_fields = state.setdefault("request_log_fields", {})
    if not isinstance(request_fields, dict):
        request_fields = {}
        state["request_log_fields"] = request_fields
    clean = {str(key): value for key, value in fields.items() if value is not None}
    reserved = {
        "component",
        "runtime_kind",
        "request_id",
        "trace_id",
        "span_id",
        "parent_span_id",
        "organization_project_environment_uid",
    }
    for key, value in clean.items():
        if key not in reserved or key not in request_fields:
            request_fields[key] = value
    clean = {key: request_fields[key] for key in clean if key in request_fields}
    bind_contextvars(**clean)


def _content_length(scope: Scope) -> int | None:
    value = _request_field(scope, b"content-length")
    try:
        parsed = int(value) if value is not None else None
    except ValueError:
        return None
    return parsed if parsed is not None and parsed >= 0 else None


def _status_class(status_code: int) -> str:
    return f"{max(1, min(status_code // 100, 9))}xx"


def _outcome(status_code: int, *, disconnected: bool = False) -> str:
    if disconnected:
        return "disconnected"
    if status_code >= 500:
        return "failed"
    if status_code >= 400:
        return "rejected"
    return "success"


class RequestContextMiddleware:
    """Bind request context and emit structured access events for ASGI traffic."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = structlog.get_logger("astro.http")
        self.active_requests = 0
        self.request_count = 0
        self._probe_health: dict[str, bool] = {}
        self._probe_failure_logged_at: dict[str, float] = {}

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        clear_contextvars()
        request_id = _request_id(scope)
        trace_id, parent_span_id, trace_sampled = _trace_context(scope)
        span_id = uuid.uuid4().hex[:16]
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["trace_id"] = trace_id
        state["span_id"] = span_id
        started_at = time.monotonic()
        method = str(scope.get("method") or "").upper()
        status_code = 500
        response_size_bytes = 0
        observed_request_size_bytes = 0
        first_byte_ms: float | None = None
        streaming = False
        disconnected = False
        response_started = False
        terminal_emitted = False
        self.active_requests += 1
        self.request_count += 1

        user_uid = _bounded_identifier(_request_field(scope, b"x-user-uid"))
        service_uid = _bounded_identifier(_request_field(scope, b"x-coding-agent-service-uid"))
        environment_uid = _bounded_identifier(
            _request_field(
                scope,
                b"x-organization-project-environment-uid",
            )
        )
        base_fields: dict[str, object] = {
            **_environment_context(),
            "component": "astro.http",
            "runtime_kind": "coding_agent",
            "request_id": request_id,
            "trace_id": trace_id,
            "span_id": span_id,
            "trace_sampled": trace_sampled,
            "http_method": method,
            "protocol": f"HTTP/{scope.get('http_version', '')}",
            "principal_type": "user" if user_uid else "anonymous",
            "auth_outcome": "authenticated" if user_uid else "anonymous",
            "active_requests_at_start": self.active_requests,
            "cold_start": self.request_count == 1,
            "process_uptime_ms": round(
                (time.monotonic() - PROCESS_STARTED_AT) * 1000,
                3,
            ),
        }
        if parent_span_id is not None:
            base_fields["parent_span_id"] = parent_span_id
        if user_uid is not None:
            base_fields["user_uid"] = user_uid
        if service_uid is not None:
            base_fields["coding_agent_service_uid"] = service_uid
        if environment_uid is not None:
            base_fields["organization_project_environment_uid"] = environment_uid
            state["organization_project_environment_uid"] = environment_uid
        request_size = _content_length(scope)
        if request_size is not None:
            base_fields["request_size_bytes"] = request_size
        bind_request_log_fields(scope, **base_fields)

        async def receive_with_context() -> Message:
            nonlocal disconnected, observed_request_size_bytes
            message = await receive()
            if message["type"] == "http.request":
                observed_request_size_bytes += len(message.get("body", b""))
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        def terminal_fields(*, outcome: str | None = None) -> dict[str, object]:
            request_fields = state.get("request_log_fields")
            fields = (
                {str(key): value for key, value in request_fields.items()}
                if isinstance(request_fields, Mapping)
                else {}
            )
            fields.update(
                {
                    "message": "HTTP request completed",
                    "http_method": method,
                    "route": _route_path(scope),
                    "status_code": status_code,
                    "status_class": _status_class(status_code),
                    "duration_ms": round(
                        (time.monotonic() - started_at) * 1000,
                        3,
                    ),
                    "response_size_bytes": response_size_bytes,
                    "is_streaming": streaming,
                    "client_disconnected": disconnected,
                    "outcome": outcome or _outcome(status_code, disconnected=disconnected),
                }
            )
            fields["request_size_bytes"] = (
                request_size if request_size is not None else observed_request_size_bytes
            )
            if status_code == 422:
                fields["validation_error_count"] = 1
            operation_id = getattr(scope.get("route"), "operation_id", None)
            if operation_id:
                fields["operation_id"] = str(operation_id)[:MAX_CORRELATION_LENGTH]
            if first_byte_ms is not None:
                fields["first_byte_ms"] = first_byte_ms
            return fields

        def log_terminal(event: str, fields: Mapping[str, object]) -> None:
            nonlocal terminal_emitted
            if terminal_emitted:
                return
            terminal_emitted = True
            probe_path = str(scope.get("path") or "")
            if probe_path in PLATFORM_PROBE_PATHS:
                outcome = str(fields.get("outcome") or "")
                raw_status = fields.get("status_code")
                status = raw_status if isinstance(raw_status, int) else 500
                healthy = outcome == "success" and status < 400
                previous = self._probe_health.get(probe_path)
                self._probe_health[probe_path] = healthy
                probe_fields = {
                    key: fields[key]
                    for key in (
                        "component",
                        "runtime_kind",
                        "request_id",
                        "trace_id",
                        "span_id",
                        "status_code",
                        "status_class",
                        "duration_ms",
                        "outcome",
                    )
                    if key in fields
                }
                probe_fields["probe_path"] = probe_path
                if healthy:
                    if previous is False:
                        safe_log(
                            self.logger,
                            "info",
                            "runtime.probe.recovered",
                            message="Runtime probe recovered",
                            **probe_fields,
                        )
                    return

                now = time.monotonic()
                last_logged_at = self._probe_failure_logged_at.get(probe_path)
                should_log = (
                    previous is not False
                    or last_logged_at is None
                    or now - last_logged_at >= PROBE_FAILURE_LOG_INTERVAL_SECONDS
                )
                if should_log:
                    self._probe_failure_logged_at[probe_path] = now
                    safe_log(
                        self.logger,
                        "error" if status >= 500 else "warning",
                        "runtime.probe.failed",
                        message="Runtime probe failed",
                        **probe_fields,
                    )
                return
            if fields.get("outcome") == "failed":
                safe_log(self.logger, "error", event, **dict(fields))
            elif fields.get("outcome") in {"rejected", "cancelled", "disconnected"}:
                safe_log(self.logger, "warning", event, **dict(fields))
            else:
                safe_log(self.logger, "info", event, **dict(fields))

        async def send_with_context(message: Message) -> None:
            nonlocal first_byte_ms, response_size_bytes, response_started
            nonlocal status_code, streaming
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
                headers = list(message.get("headers", ()))
                if not any(name.lower() == b"x-request-id" for name, _value in headers):
                    headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
                first_byte_ms = round((time.monotonic() - started_at) * 1000, 3)
                for name, value in headers:
                    if name.lower() == b"content-type":
                        bind_request_log_fields(
                            scope,
                            content_type=value.decode("latin-1").split(";", 1)[0],
                        )
                        break
            elif message["type"] == "http.response.body":
                response_size_bytes += len(message.get("body", b""))
                streaming = streaming or bool(message.get("more_body", False))

            await send(message)

        try:
            await self.app(scope, receive_with_context, send_with_context)
            log_terminal("http.request.completed", terminal_fields())
        except asyncio.CancelledError:
            log_terminal(
                "http.request.cancelled",
                terminal_fields(outcome="cancelled"),
            )
            raise
        except Exception as error:
            fields = terminal_fields(outcome="failed")
            fields.update(
                message="HTTP request failed",
                error_type=type(error).__name__,
                response_started=response_started,
            )
            log_terminal("http.request.failed", fields)
            raise
        finally:
            self.active_requests -= 1
            clear_contextvars()


__all__ = [
    "RequestContextMiddleware",
    "bind_request_log_fields",
    "configure_logging",
    "conversation_log_fields",
    "safe_log",
]
