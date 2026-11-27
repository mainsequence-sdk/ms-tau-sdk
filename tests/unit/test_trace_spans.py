"""The Agent's hops in a platform trace (ADR 0031).

A trace is the set of log lines that share one W3C trace id; each lifecycle's terminal line is its
span. The runtime's request, run, turn, model call, tool call, handoff and platform or application
call are each a span, a child of the span it starts in, and the current span is the parent the
runtime names on the calls it makes. Model calls, hydration and lease renewals carry no context.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import structlog
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
from structlog.contextvars import bound_contextvars, clear_contextvars, merge_contextvars
from structlog.testing import capture_logs

from ms_tau_sdk import create_app
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.models import TauTurnCommit
from ms_tau_sdk.logging import TraceSpan, configure_logging
from ms_tau_sdk.protocols.caller_context import (
    caller_context_scope,
    outbound_headers,
    request_context,
    span_scope,
)
from ms_tau_sdk.runtime.events import TauRuntimeEvent
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.runtime.observability import TauTurnObserver
from ms_tau_sdk.runtime.session import ActiveSessionRuntime
from ms_tau_sdk.settings import TauSDKSettings

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
CALLER_SPAN_ID = "00f067aa0ba902b7"
TRACEPARENT = f"00-{TRACE_ID}-{CALLER_SPAN_ID}-01"
REQUEST_SPAN_ID = "b7ad6b7169203331"


def _log_events(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def _parent(headers: Any) -> tuple[str, str, str]:
    """The trace id, parent span id and flags a call's ``traceparent`` names."""

    version, trace_id, span_id, flags = headers["traceparent"].split("-")
    assert version == "00"
    return trace_id, span_id, flags


def _in_request_span(**fields: object) -> Any:
    """Log context and current span as the request middleware leaves them for a request's work."""

    return bound_contextvars(
        trace_id=TRACE_ID,
        span_id=REQUEST_SPAN_ID,
        parent_span_id=CALLER_SPAN_ID,
        trace_sampled=True,
        **fields,
    )


@pytest.fixture(autouse=True)
def _no_log_context() -> Any:
    clear_contextvars()
    yield
    clear_contextvars()


# --- The request span -------------------------------------------------------------------------


async def test_the_request_span_is_the_parent_of_the_calls_made_for_it(
    test_settings, asgi_client, capsys
):
    app = create_app(test_settings)
    sent: list[dict[str, str]] = []

    @app.get("/calls")
    async def calls() -> dict[str, str]:
        sent.append(outbound_headers())
        return {}

    async with asgi_client(app) as http:
        response = await http.get(
            "/calls", headers={"traceparent": TRACEPARENT, "tracestate": "portal=7"}
        )

    assert response.status_code == 200
    completed = next(
        event
        for event in _log_events(capsys.readouterr().out)
        if event["event"] == "http.request.completed"
    )
    # The caller's span is the request span's recorded parent, never the parent the runtime sends.
    assert completed["trace_id"] == TRACE_ID
    assert completed["parent_span_id"] == CALLER_SPAN_ID
    assert completed["span_id"] != CALLER_SPAN_ID
    [headers] = sent
    assert _parent(headers) == (TRACE_ID, completed["span_id"], "01")
    assert headers["tracestate"] == "portal=7"
    # The request's span ends with the request.
    assert not trace.get_current_span().get_span_context().is_valid


async def test_a_trace_the_runtime_starts_is_sent_on_as_sampled(test_settings, asgi_client, capsys):
    app = create_app(test_settings)
    sent: list[dict[str, str]] = []

    @app.get("/calls")
    async def calls() -> dict[str, str]:
        sent.append(outbound_headers())
        return {}

    async with asgi_client(app) as http:
        await http.get("/calls")

    completed = next(
        event
        for event in _log_events(capsys.readouterr().out)
        if event["event"] == "http.request.completed"
    )
    assert "parent_span_id" not in completed
    assert completed["trace_sampled"] is True
    assert _parent(sent[0]) == (completed["trace_id"], completed["span_id"], "01")


async def test_an_unsampled_callers_flag_is_kept(test_settings, asgi_client):
    app = create_app(test_settings)
    sent: list[dict[str, str]] = []

    @app.get("/calls")
    async def calls() -> dict[str, str]:
        sent.append(outbound_headers())
        return {}

    async with asgi_client(app) as http:
        await http.get("/calls", headers={"traceparent": f"00-{TRACE_ID}-{CALLER_SPAN_ID}-00"})

    trace_id, span_id, flags = _parent(sent[0])
    assert (trace_id, flags) == (TRACE_ID, "00")
    assert span_id != CALLER_SPAN_ID


async def test_a_verified_requests_context_keeps_the_request_span(
    served_platform_keys, tmp_path, asgi_client, capsys
):
    app = create_app(served_platform_keys.hosted_settings(tmp_path))
    sent: list[dict[str, str]] = []

    @app.get("/calls")
    async def calls() -> dict[str, str]:
        sent.append(outbound_headers())
        return {}

    async with asgi_client(app) as http:
        response = await http.get(
            "/calls",
            headers={
                "X-MainSequence-Caller-Assertion": served_platform_keys.caller_assertion(),
                "traceparent": TRACEPARENT,
                "baggage": "mainsequence.portal.user=u-81",
            },
        )

    assert response.status_code == 200
    completed = next(
        event
        for event in _log_events(capsys.readouterr().out)
        if event["event"] == "http.request.completed"
    )
    [headers] = sent
    assert _parent(headers) == (TRACE_ID, completed["span_id"], "01")
    assert headers["baggage"] == "mainsequence.portal.user=u-81"


def test_without_a_current_span_the_requests_trace_parent_is_kept():
    # A WebSocket has no request span: its traceparent is the parent its calls name.
    context = request_context([(b"traceparent", TRACEPARENT.encode())])

    with caller_context_scope(context):
        assert outbound_headers()["traceparent"] == TRACEPARENT


def test_a_span_that_instrumentation_records_stays_current():
    recording = Mock(spec=trace.Span)
    recording.is_recording.return_value = True
    recording.get_span_context.return_value = SpanContext(
        int(TRACE_ID, 16), int("1111111111111111", 16), is_remote=False, trace_flags=TraceFlags(1)
    )

    with caller_context_scope(trace.set_span_in_context(recording, Context())):
        with span_scope(TRACE_ID, REQUEST_SPAN_ID):
            assert trace.get_current_span() is recording
        context = request_context([(b"traceparent", TRACEPARENT.encode())])

    assert trace.get_current_span(context) is recording


def test_ids_that_are_not_w3c_ids_change_nothing():
    for trace_id, span_id in (("", REQUEST_SPAN_ID), ("0" * 32, REQUEST_SPAN_ID), (TRACE_ID, "x")):
        with span_scope(trace_id, span_id):
            assert outbound_headers() == {}


# --- Child spans --------------------------------------------------------------------------------


def test_a_child_span_is_current_inside_the_span_it_starts_in():
    with (
        _in_request_span(),
        span_scope(TRACE_ID, REQUEST_SPAN_ID),
        capture_logs(processors=[merge_contextvars]) as lines,
    ):
        with TraceSpan() as span:
            structlog.get_logger("portal").info("portal.inside")
            inside = outbound_headers()
        structlog.get_logger("portal").info("portal.after")
        after = outbound_headers()

    assert span.parent_span_id == REQUEST_SPAN_ID
    assert span.span_id not in (None, REQUEST_SPAN_ID)
    assert len(span.span_id or "") == 16
    assert _parent(inside) == (TRACE_ID, span.span_id, "01")
    assert _parent(after) == (TRACE_ID, REQUEST_SPAN_ID, "01")
    during, outside = lines
    assert (during["span_id"], during["parent_span_id"]) == (span.span_id, REQUEST_SPAN_ID)
    assert (outside["span_id"], outside["parent_span_id"]) == (REQUEST_SPAN_ID, CALLER_SPAN_ID)


def test_outside_a_trace_there_is_no_span():
    with TraceSpan() as span:
        headers = outbound_headers()

    assert span.log_fields == {}
    assert headers == {}


def test_a_child_span_keeps_the_trace_unsampled_when_it_is():
    remote = NonRecordingSpan(
        SpanContext(int(TRACE_ID, 16), int(REQUEST_SPAN_ID, 16), is_remote=False)
    )
    with (
        _in_request_span(),
        caller_context_scope(trace.set_span_in_context(remote, Context())),
        TraceSpan(),
    ):
        assert _parent(outbound_headers())[2] == "00"


async def test_detached_work_is_in_the_span_that_started_it(tmp_path):
    manager = SessionRuntimeManager(
        settings=TauSDKSettings(
            _env_file=None,
            runtime_credential_id="credential-id",
            workspace=tmp_path,
            startup_dependencies_enabled=False,
        ),
        backend=AsyncMock(),
        providers=Mock(),
    )

    async def observe() -> tuple[dict[str, object], dict[str, str]]:
        return dict(structlog.contextvars.get_contextvars()), outbound_headers()

    with _in_request_span(), span_scope(TRACE_ID, REQUEST_SPAN_ID):
        detached = manager.create_background_task(observe(), name="detached")
        # A Task attempt's context names the dispatch request's span too.
        task_context = request_context([(b"traceparent", TRACEPARENT.encode())])
        attempt, _ = manager.create_a2a_task_execution(
            "task-1", observe(), name="attempt", caller_context=task_context
        )

    for fields, headers in (await detached, await attempt):
        # Detached work writes no terminal line, so it is no span of its own.
        assert (fields["span_id"], fields["parent_span_id"]) == (REQUEST_SPAN_ID, CALLER_SPAN_ID)
        assert _parent(headers) == (TRACE_ID, REQUEST_SPAN_ID, "01")
    await manager.aclose()


# --- Model, tool and handoff spans -----------------------------------------------------------


def test_model_tool_and_handoff_calls_are_spans_of_the_turn():
    turn = TraceSpan
    with _in_request_span(), span_scope(TRACE_ID, REQUEST_SPAN_ID), turn() as turn_span:
        observer = TauTurnObserver(provider="openai", model="controlled-model")
        with capture_logs(processors=[merge_contextvars]) as lines:
            observer.observe(TauRuntimeEvent(type="message_start"))
            observer.observe(TauRuntimeEvent(type="model_rate_limited", data={"status_code": 429}))
            observer.observe(TauRuntimeEvent(type="message_start"))
            during_model = outbound_headers()
            observer.observe(TauRuntimeEvent(type="message_end", data={}))
            observer.observe(
                TauRuntimeEvent(
                    type="tool_execution_start",
                    data={"toolCallId": "tool-1", "toolName": "lookup"},
                )
            )
            # The tool runs between its start and end events, in the same task.
            during_tool = outbound_headers()
            structlog.get_logger("portal.tool").info("portal.tool.ran")
            observer.observe(
                TauRuntimeEvent(
                    type="tool_execution_end",
                    data={"toolCallId": "tool-1", "toolName": "lookup"},
                )
            )
            after_tool = outbound_headers()
            observer.observe(TauRuntimeEvent(type="handoff_started", data={"handoff_uid": "h-1"}))
            observer.observe(TauRuntimeEvent(type="handoff_completed", data={"handoff_uid": "h-1"}))
            observer.close()

    by_event: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        by_event.setdefault(line["event"], []).append(line)
    turn_id = turn_span.span_id
    spans = set()
    for name in (
        "agent.model.started",
        "agent.model.failed",
        "agent.model.completed",
        "agent.tool.started",
        "agent.tool.completed",
        "agent.handoff.started",
        "agent.handoff.completed",
    ):
        for line in by_event[name]:
            assert line["parent_span_id"] == turn_id
            assert line["trace_id"] == TRACE_ID
            spans.add((name.rsplit(".", 1)[0], line["span_id"]))
    model_spans = {span for kind, span in spans if kind == "agent.model"}
    # One span per model attempt: the rate-limited attempt and the retry.
    assert len(model_spans) == 2
    [failed] = by_event["agent.model.failed"]
    [completed] = by_event["agent.model.completed"]
    assert failed["model_call_uid"] != completed["model_call_uid"]
    assert failed["span_id"] != completed["span_id"]
    # Every terminal line gives its span's duration as duration_ms.
    for line in (failed, completed, *by_event["agent.tool.completed"]):
        assert isinstance(line["duration_ms"], float)
    assert completed["duration_ms"] == completed["model_duration_ms"]
    tool_span = by_event["agent.tool.completed"][0]["span_id"]
    assert by_event["agent.tool.started"][0]["span_id"] == tool_span
    assert (
        by_event["agent.handoff.started"][0]["span_id"]
        == (by_event["agent.handoff.completed"][0]["span_id"])
    )
    assert len({span for _kind, span in spans}) == 4
    # A model call names no parent; a tool's calls name the tool; its log lines are in its span.
    assert _parent(during_model)[1] == turn_id
    assert _parent(during_tool)[1] == tool_span
    assert by_event["portal.tool.ran"][0]["span_id"] == tool_span
    assert _parent(after_tool)[1] == turn_id


def test_a_tool_call_that_never_ends_leaves_the_turn_when_the_turn_ends():
    with _in_request_span(), span_scope(TRACE_ID, REQUEST_SPAN_ID):
        observer = TauTurnObserver(provider="openai", model="controlled-model")
        observer.observe(
            TauRuntimeEvent(
                type="tool_execution_start", data={"toolCallId": "tool-1", "toolName": "lookup"}
            )
        )
        assert _parent(outbound_headers())[1] != REQUEST_SPAN_ID
        observer.close()
        observer.close()

        assert _parent(outbound_headers())[1] == REQUEST_SPAN_ID
        assert structlog.contextvars.get_contextvars()["span_id"] == REQUEST_SPAN_ID


# --- Run and turn spans -------------------------------------------------------------------------


class _ToolCallingCodingSession:
    """A Tau session whose turn makes one model call and one tool call that calls the platform."""

    is_running = False

    def __init__(self, backend: MainSequenceClient) -> None:
        self.backend = backend

    async def prompt(self, _content: str) -> Any:
        yield {"type": "message_start"}
        yield {"type": "message_end"}
        yield {"type": "tool_execution_start", "toolCallId": "tool-1", "toolName": "lookup"}
        await self.backend.get_user("user-1")
        yield {"type": "tool_execution_end", "toolCallId": "tool-1", "toolName": "lookup"}
        yield {"type": "agent_settled"}

    def cancel(self) -> None:
        return None


def _backend(tmp_path: Any, sent: list[httpx.Request]) -> MainSequenceClient:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"uid": "user-1"})

    token_file = tmp_path / "token"
    token_file.write_text("token", encoding="utf-8")
    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="credential-id",
        runtime_identity_token_file=token_file,
    )
    auth = AsyncMock()
    auth.headers.return_value = {"Authorization": "Bearer runtime"}
    auth.bind_client = Mock()
    http = httpx.AsyncClient(base_url=settings.backend_url, transport=httpx.MockTransport(handle))
    return MainSequenceClient(settings, auth, client=http)


async def test_a_turns_spans_nest_under_the_request(tmp_path):
    sent: list[httpx.Request] = []
    platform = _backend(tmp_path, sent)
    manager = SessionRuntimeManager(
        settings=TauSDKSettings(
            _env_file=None,
            runtime_credential_id="credential-id",
            workspace=tmp_path,
            startup_dependencies_enabled=False,
        ),
        backend=AsyncMock(),
        providers=Mock(),
    )
    manager._runtimes["session-1"] = ActiveSessionRuntime(
        session_uid="session-1",
        holder_id=manager.holder_id,
        coding_session=_ToolCallingCodingSession(platform),
        storage=SimpleNamespace(
            lease_token="lease",
            begin_turn=AsyncMock(),
            commit_turn=AsyncMock(
                side_effect=lambda **kwargs: TauTurnCommit(
                    turn_uid=kwargs["turn_uid"], next_sequence=0, committed_at=datetime.now(UTC)
                )
            ),
            entries_at_sequence=AsyncMock(return_value=[]),
            flush=AsyncMock(),
            invalidate_lease=Mock(),
        ),
        provider=object(),
        provider_name="test-provider",
        model="test-model",
        agent_uid="agent-1",
        runtime_config_sha256="sha256:runtime",
    )

    with (
        _in_request_span(),
        span_scope(TRACE_ID, REQUEST_SPAN_ID),
        capture_logs(processors=[merge_contextvars]) as lines,
    ):
        async for _event in manager.prompt("session-1", "hello"):
            pass
        after = outbound_headers()
        assert structlog.contextvars.get_contextvars()["span_id"] == REQUEST_SPAN_ID

    manager._runtimes.clear()
    await manager.aclose()
    await platform._client.aclose()

    def span_of(event: str) -> tuple[str, str]:
        [line] = [line for line in lines if line["event"] == event]
        return line["span_id"], line["parent_span_id"]

    run, run_parent = span_of("agent.run.completed")
    turn, turn_parent = span_of("agent.turn.completed")
    model, model_parent = span_of("agent.model.completed")
    tool, tool_parent = span_of("agent.tool.completed")
    call, call_parent = span_of("dependency.call.completed")
    assert run_parent == REQUEST_SPAN_ID
    assert span_of("agent.run.accepted") == span_of("agent.run.started") == (run, run_parent)
    assert turn_parent == run
    assert span_of("agent.turn.started") == (turn, turn_parent)
    assert model_parent == turn and tool_parent == turn
    # The tool's platform call is a span of the tool, and the platform is told so.
    assert call_parent == tool
    [request] = sent
    assert _parent(request.headers) == (TRACE_ID, call, "01")
    assert len({REQUEST_SPAN_ID, run, turn, model, tool, call}) == 6
    # Nothing persisted changes: the uids stay what they are.
    [completed] = [line for line in lines if line["event"] == "agent.run.completed"]
    assert completed["agent_run_uid"] != run
    assert _parent(after) == (TRACE_ID, REQUEST_SPAN_ID, "01")


# --- Platform and application calls -------------------------------------------------------------


async def test_each_platform_call_attempt_is_a_span_of_the_work(tmp_path):
    answers = iter([503, 200, 200])
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(next(answers), json={"uid": "user-1"})

    token_file = tmp_path / "token"
    token_file.write_text("token", encoding="utf-8")
    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="credential-id",
        runtime_identity_token_file=token_file,
    )
    auth = AsyncMock()
    auth.headers.return_value = {"Authorization": "Bearer runtime"}
    auth.bind_client = Mock()
    http = httpx.AsyncClient(base_url=settings.backend_url, transport=httpx.MockTransport(handle))
    client = MainSequenceClient(settings, auth, client=http)

    with (
        _in_request_span(),
        span_scope(TRACE_ID, REQUEST_SPAN_ID),
        capture_logs(processors=[merge_contextvars]) as lines,
    ):
        await client.get_user("user-1")
        answer = await client.platform_request("GET", "/api/v1/data-nodes/", proof=None)
    await http.aclose()

    calls = [line for line in lines if line["event"] == "dependency.call.completed"]
    assert len(calls) == 3
    assert all(line["parent_span_id"] == REQUEST_SPAN_ID for line in calls)
    assert len({line["span_id"] for line in calls}) == 3
    for request, line in zip(sent, calls, strict=True):
        assert _parent(request.headers) == (TRACE_ID, line["span_id"], "01")
    # The retry warning between attempts is in the work's span.
    [retrying] = [line for line in lines if line["event"] == "backend.request.retrying"]
    assert retrying["span_id"] == REQUEST_SPAN_ID
    assert answer.status_code == 200


async def test_a_lease_renewal_names_no_span(tmp_path):
    manager = SessionRuntimeManager(
        settings=TauSDKSettings(
            _env_file=None,
            runtime_credential_id="credential-id",
            workspace=tmp_path,
            startup_dependencies_enabled=False,
            runtime_lease_renew_interval_seconds=1,
            runtime_lease_ttl_seconds=30,
        ),
        backend=AsyncMock(),
        providers=Mock(),
    )
    runtime = SimpleNamespace(
        session_uid="session-1",
        evicting=False,
        storage=SimpleNamespace(lease_token="lease", replace_lease_token=Mock()),
    )
    renewed_with: list[tuple[dict[str, str], dict[str, str]]] = []

    async def renew(*_args: object) -> SimpleNamespace:
        with TraceSpan() as span:
            renewed_with.append((outbound_headers(), span.log_fields))
        runtime.evicting = True
        return SimpleNamespace(
            lease_token="lease-2",
            cancel_requested=False,
            checkpoint_version=1,
            lease_expires_at=None,
        )

    manager.backend.renew_runtime_lease.side_effect = renew  # type: ignore[attr-defined]
    with _in_request_span(), span_scope(TRACE_ID, REQUEST_SPAN_ID), TraceSpan():
        renewal = asyncio.create_task(manager._renew_lease(runtime))  # type: ignore[arg-type]
    await asyncio.wait_for(renewal, timeout=5)

    assert renewed_with == [({}, {})]


# --- What the runtime no longer writes ----------------------------------------------------------


def test_no_cloud_provider_trace_field_is_written(capsys, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    configure_logging("INFO", machine_sink=True, human_sink=False)

    with _in_request_span(), span_scope(TRACE_ID, REQUEST_SPAN_ID):
        structlog.get_logger("ms_tau_sdk.trace").info("trace.event")

    [event] = [e for e in _log_events(capsys.readouterr().out) if e["event"] == "trace.event"]
    assert (event["trace_id"], event["span_id"]) == (TRACE_ID, REQUEST_SPAN_ID)
    assert not [key for key in event if key.startswith("logging.googleapis.com/")]
