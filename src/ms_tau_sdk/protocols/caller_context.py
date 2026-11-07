"""Caller context: the W3C ``traceparent`` and ``baggage`` that come with a request or a Task.

A service user that calls Main Sequence for one of its own users can say, in W3C Baggage, which
user the call is for and add its own information about the call. The platform carries that
context to the applications that run on it, an Agent among them, and records it; it never
authenticates, authorizes or decides anything with it, and it defines no keys. The runtime carries
every key the same way and gives none of them a meaning.

The context of the request or Task the runtime serves is OpenTelemetry's current context: Agent
code reads it with ``opentelemetry.baggage``, and the runtime's calls to the platform and to
applications carry it on. It is a claim, worth as much as the trust in the caller that delivered
it, and it is never Main Sequence identity.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry import baggage, trace
from opentelemetry import context as otel_context
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_baggage_propagator = W3CBaggagePropagator()
_trace_propagator = TraceContextTextMapPropagator()
_CARRIED_HEADERS = frozenset({"traceparent", "tracestate", "baggage"})
_handling_baggage: ContextVar[bool] = ContextVar("ms_tau_sdk_handling_baggage", default=False)


class _QuietBaggagePropagator(logging.Filter):
    """Drop the parser's warnings while the runtime reads or writes baggage: they quote it."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _handling_baggage.get()


logging.getLogger("opentelemetry.baggage.propagation").addFilter(_QuietBaggagePropagator())


def _carrier(headers: Iterable[tuple[bytes, bytes]]) -> dict[str, str]:
    """The request's trace context and baggage headers; repeated fields are joined."""

    carrier: dict[str, str] = {}
    for raw_name, raw_value in headers:
        name = raw_name.decode("latin-1").lower()
        if name in _CARRIED_HEADERS:
            value = raw_value.decode("latin-1")
            carrier[name] = f"{carrier[name]},{value}" if name in carrier else value
    return carrier


def _with_baggage_header(header: str | None, context: Context) -> Context:
    """``context`` with the members of one W3C ``baggage`` header added.

    The parser's limits apply: a header over 8192 bytes is ignored whole, a member over 4096 bytes
    is skipped and at most 180 members are read. W3C properties, after a member's ``;``, are
    dropped, as the platform drops them. Malformed input is skipped and never raises.
    """

    # The header is decoded as latin-1, so its length is its length in bytes.
    if not header or len(header) > W3CBaggagePropagator._MAX_HEADER_LENGTH:
        return context
    # Properties follow an unencoded ';', so an encoded ';' inside a value survives.
    members = ",".join(member.split(";", 1)[0] for member in header.split(","))
    token = _handling_baggage.set(True)
    try:
        return _baggage_propagator.extract({"baggage": members}, context=context)
    except Exception:
        return context
    finally:
        _handling_baggage.reset(token)


def request_context(headers: Iterable[tuple[bytes, bytes]]) -> Context:
    """The caller context one inbound request or Task dispatch carries, from its headers.

    Its baggage is exactly the request's ``baggage`` header: never the process's or another
    request's, and none without the header. Its trace parent is the request's ``traceparent``,
    unless instrumentation already records a span for the request.
    """

    carrier = _carrier(headers)
    context = baggage.clear(context=otel_context.get_current())
    context = _with_baggage_header(carrier.get("baggage"), context)
    if not trace.get_current_span(context).is_recording():
        context = _trace_propagator.extract(carrier, context=context)
    return context


def outbound_headers(present: Iterable[str] = ()) -> dict[str, str]:
    """The W3C ``traceparent``, ``tracestate`` and ``baggage`` headers of the current context.

    Empty when the current context has neither. A header named in ``present``, in any case, is
    left out: the call sets it itself, and its own value wins. Building them never fails a call.
    """

    carrier: dict[str, str] = {}
    token = _handling_baggage.set(True)
    try:
        _trace_propagator.inject(carrier)
        _baggage_propagator.inject(carrier)
    except Exception:
        return {}
    finally:
        _handling_baggage.reset(token)
    own = {str(name).lower() for name in present}
    return {name: value for name, value in carrier.items() if name not in own}


def recorded_baggage(context: Context | None = None) -> dict[str, str]:
    """The context's baggage keys under ``mainsequence.``: the keys log lines record."""

    return {
        key: str(value)
        for key, value in baggage.get_all(context=context).items()
        if key.startswith("mainsequence.")
    }


@contextmanager
def caller_context_scope(context: Context) -> Iterator[None]:
    """Make ``context`` the current caller context until the block ends."""

    token = otel_context.attach(context)
    try:
        yield
    finally:
        otel_context.detach(token)
