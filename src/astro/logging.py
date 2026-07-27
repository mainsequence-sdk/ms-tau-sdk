"""Structlog configuration aligned with the Main Sequence Django backend."""

from __future__ import annotations

import asyncio
import hashlib
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

SENSITIVE_KEY_PARTS = (
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)
BEARER_PATTERN = re.compile(
    r"(?i)\bbearer\s+(?!bearer\b)[A-Za-z0-9._~+/=-]+"
)
CALLSITE_PARAMETERS = {
    structlog.processors.CallsiteParameter.PATHNAME,
    structlog.processors.CallsiteParameter.FILENAME,
    structlog.processors.CallsiteParameter.MODULE,
    structlog.processors.CallsiteParameter.LINENO,
    structlog.processors.CallsiteParameter.FUNC_NAME,
}
CONVERSATION_EXCERPT_MAX_CHARS = 200


def conversation_log_fields(
    prompt: str,
    *,
    include_excerpt: bool,
    message_count: int | None = None,
) -> dict[str, object]:
    """Build safe-by-default metadata for a validated conversation prompt."""

    fields: dict[str, object] = {
        "prompt_chars": len(prompt),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }
    if message_count is not None:
        fields["message_count"] = message_count
    if not include_excerpt:
        return fields

    normalized = " ".join(prompt.split())
    if len(normalized) > CONVERSATION_EXCERPT_MAX_CHARS:
        normalized = (
            normalized[: CONVERSATION_EXCERPT_MAX_CHARS - 3].rstrip() + "..."
        )
    fields["prompt_excerpt"] = normalized
    return fields


def _sanitize(value: object, *, key: str = "") -> object:
    if any(part in key.lower() for part in SENSITIVE_KEY_PARTS):
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
        event_dict["logging.googleapis.com/trace"] = (
            f"projects/{project_id}/traces/{trace_id}"
        )
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


def _sanitize_event(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    return {
        str(key): _sanitize(value, key=str(key))
        for key, value in event_dict.items()
    }


class ClickableConsoleRenderer(structlog.dev.ConsoleRenderer):
    """Django-compatible console rendering with a clickable source line."""

    def __call__(
        self,
        logger: WrappedLogger,
        method_name: str,
        event_dict: EventDict,
    ) -> str:
        source = event_dict.pop("source", None)
        rendered = super().__call__(logger, method_name, event_dict)
        return f"{rendered}\n{source}" if source else rendered


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


def _route_path(scope: Scope) -> str:
    route = scope.get("route")
    route_path = getattr(route, "path", None)
    return str(route_path or scope.get("path") or "")


class RequestContextMiddleware:
    """Bind request context and emit structured access events for ASGI traffic."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = structlog.get_logger("astro.http")

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        bind_contextvars(request_id=request_id)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        started_at = time.monotonic()
        method = str(scope.get("method") or "")
        path = str(scope.get("path") or "")
        client = scope.get("client")
        remote_address = str(client[0]) if client else None
        user_agent = _request_field(scope, b"user-agent")
        status_code = 500
        response_size_bytes = 0
        completed = False

        self.logger.info(
            "http.request.started",
            method=method,
            path=path,
            remote_address=remote_address,
        )

        async def send_with_context(message: Message) -> None:
            nonlocal completed, response_size_bytes, status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", ()))
                if not any(name.lower() == b"x-request-id" for name, _value in headers):
                    headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            elif message["type"] == "http.response.body":
                response_size_bytes += len(message.get("body", b""))

            await send(message)

            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                completed = True
                fields = {
                    "method": method,
                    "route": _route_path(scope),
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": round(
                        (time.monotonic() - started_at) * 1000,
                        3,
                    ),
                    "response_size_bytes": response_size_bytes,
                    "remote_address": remote_address,
                    "user_agent": user_agent,
                }
                if status_code >= 500:
                    self.logger.error("http.request.completed", **fields)
                elif status_code >= 400:
                    self.logger.warning("http.request.completed", **fields)
                else:
                    self.logger.info("http.request.completed", **fields)

        try:
            await self.app(scope, receive, send_with_context)
        except asyncio.CancelledError:
            if not completed:
                self.logger.warning(
                    "http.request.cancelled",
                    method=method,
                    route=_route_path(scope),
                    path=path,
                    duration_ms=round(
                        (time.monotonic() - started_at) * 1000,
                        3,
                    ),
                )
            raise
        except Exception as error:
            if not completed:
                self.logger.exception(
                    "http.request.failed",
                    method=method,
                    route=_route_path(scope),
                    path=path,
                    status_code=status_code,
                    duration_ms=round(
                        (time.monotonic() - started_at) * 1000,
                        3,
                    ),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
            raise
        finally:
            clear_contextvars()
