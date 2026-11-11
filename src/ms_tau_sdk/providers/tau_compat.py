"""Version-locked compatibility patches for defects in the imported Tau runtime.

The patch in this module is deliberately narrow.  ``tau-ai==0.4.2`` constructs
an empty terminal ``ProviderErrorEvent`` after an ``httpx.HTTPError`` exhausts
its retries, even though the concrete exception is still active.  Main
Sequence cannot recover that information after Tau has canonicalized the
provider stream, so we enrich the event at its construction boundary without
replacing Tau's provider implementation.

Upstream report: https://github.com/huggingface/tau/discussions/745
"""

from __future__ import annotations

import sys
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from importlib.metadata import version
from typing import Any

import httpx
import structlog
from tau_agent.types import JSONValue
from tau_ai import openai_compatible as tau_openai_compatible
from tau_ai._provider_events import (
    ProviderErrorEvent,
    ProviderEvent,
    ProviderRetryEvent,
)

from ms_tau_sdk.logging import safe_log

AFFECTED_TAU_AI_VERSION = "0.4.2"
UPSTREAM_REPORT_URL = "https://github.com/huggingface/tau/discussions/745"
_PATCH_MARKER = "__ms_tau_sdk_provider_error_compat__"
_logger = structlog.get_logger("ms_tau_sdk.providers.tau_compat")


def _transport_phase(error: httpx.HTTPError) -> str:
    if isinstance(error, (httpx.ConnectTimeout, httpx.ConnectError, httpx.ProxyError)):
        return "connect"
    if isinstance(error, (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError)):
        return "read"
    if isinstance(error, (httpx.WriteTimeout, httpx.WriteError)):
        return "write"
    if isinstance(error, httpx.PoolTimeout):
        return "pool"
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.ProtocolError):
        return "protocol"
    return "transport"


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _fallback_message(error_type: str, attempts: int | None) -> str:
    if attempts is None:
        return error_type
    suffix = "attempt" if attempts == 1 else "attempts"
    return f"{error_type} after {attempts} {suffix}"


def _safe_retry_summary(events: list[ProviderRetryEvent]) -> list[JSONValue]:
    summary: list[JSONValue] = []
    for event in events:
        data = event.data if isinstance(event.data, Mapping) else {}
        item: dict[str, JSONValue] = {
            "attempt": event.attempt,
            "max_attempts": event.max_attempts,
            "delay_seconds": event.delay_seconds,
        }
        error_type = data.get("error_type")
        if isinstance(error_type, str) and error_type.strip():
            item["error_type"] = error_type[:128]
        status_code = data.get("status_code")
        if isinstance(status_code, int) and not isinstance(status_code, bool):
            item["status_code"] = status_code
        summary.append(item)
    return summary


def install_openai_compatible_provider_error_patch() -> None:
    """Install the reviewed Tau 0.4.2 diagnostic patch exactly once.

    A Tau upgrade must review and either remove or explicitly retarget this
    private-boundary patch.  Silently applying it to another version would be
    more dangerous than failing during provider construction.
    """

    installed_version = version("tau-ai")
    if installed_version != AFFECTED_TAU_AI_VERSION:
        raise RuntimeError(
            "The ms-tau-sdk Tau provider-error compatibility patch only supports "
            f"tau-ai=={AFFECTED_TAU_AI_VERSION}; found {installed_version}. Review "
            f"the upstream resolution at {UPSTREAM_REPORT_URL} before changing the pin."
        )

    compatibility_module: Any = tau_openai_compatible
    provider_class: Any = tau_openai_compatible.OpenAICompatibleProvider
    current_factory = compatibility_module.ProviderErrorEvent
    current_stream = provider_class._stream
    factory_patched = bool(getattr(current_factory, _PATCH_MARKER, False))
    stream_patched = bool(getattr(current_stream, _PATCH_MARKER, False))
    if factory_patched and stream_patched:
        return
    if factory_patched or stream_patched:
        raise RuntimeError("Tau provider-error compatibility patch is only partially installed")
    if current_factory is not ProviderErrorEvent:
        raise RuntimeError(
            "Tau ProviderErrorEvent constructor changed; compatibility review required"
        )

    original_stream = current_stream

    def patched_provider_error_event(*args: Any, **kwargs: Any) -> ProviderErrorEvent:
        active_error = sys.exception()
        if isinstance(active_error, httpx.HTTPError):
            details = kwargs.get("data")
            diagnostic = dict(details) if isinstance(details, Mapping) else {}
            error_type = type(active_error).__name__
            phase = _transport_phase(active_error)
            attempts = _positive_int(diagnostic.get("attempts"))
            diagnostic.setdefault("error_type", error_type)
            diagnostic.setdefault("transport_phase", phase)
            diagnostic.setdefault("retry_exhausted", True)
            diagnostic.setdefault("diagnostic_source", "ms-tau-sdk-tau-compat")
            failure_uid = str(diagnostic.setdefault("failure_uid", str(uuid.uuid4())))
            kwargs["data"] = diagnostic

            if not str(kwargs.get("message") or "").strip():
                kwargs["message"] = _fallback_message(error_type, attempts)
                diagnostic["message_fallback"] = True

            safe_log(
                _logger,
                "error",
                "dependency.call.failed",
                message="Tau provider HTTP request failed",
                dependency_name="tau-ai",
                dependency_version=installed_version,
                provider_error_type=error_type,
                transport_phase=phase,
                provider_attempts=attempts,
                retry_exhausted=True,
                compatibility_patch="tau-ai-0.4.2-provider-error",
                failure_uid=failure_uid,
                exc_info=(type(active_error), active_error, active_error.__traceback__),
            )
        return current_factory(*args, **kwargs)

    def patched_stream(*args: Any, **kwargs: Any) -> AsyncIterator[ProviderEvent]:
        source = original_stream(*args, **kwargs)

        async def iterator() -> AsyncIterator[ProviderEvent]:
            started_at = time.monotonic()
            retry_events: list[ProviderRetryEvent] = []
            async for event in source:
                if isinstance(event, ProviderRetryEvent):
                    retry_events.append(event)
                if isinstance(event, ProviderErrorEvent):
                    diagnostic = dict(event.data or {})
                    diagnostic.setdefault(
                        "provider_duration_ms",
                        round((time.monotonic() - started_at) * 1000, 3),
                    )
                    if retry_events:
                        diagnostic.setdefault("retry_history", _safe_retry_summary(retry_events))
                        diagnostic.setdefault("retry_exhausted", True)
                    yield event.model_copy(update={"data": diagnostic})
                    continue
                yield event

        return iterator()

    setattr(patched_provider_error_event, _PATCH_MARKER, True)
    setattr(patched_stream, _PATCH_MARKER, True)
    compatibility_module.ProviderErrorEvent = patched_provider_error_event
    provider_class._stream = patched_stream


__all__ = [
    "AFFECTED_TAU_AI_VERSION",
    "UPSTREAM_REPORT_URL",
    "install_openai_compatible_provider_error_patch",
]
