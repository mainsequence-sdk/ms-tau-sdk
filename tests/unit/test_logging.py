import json
import logging

import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route
from structlog.contextvars import bind_contextvars, clear_contextvars

from astro.app import create_app
from astro.logging import (
    RequestContextMiddleware,
    bind_request_log_fields,
    configure_logging,
    conversation_log_fields,
)
from astro.runtime.events import AstroRuntimeEvent
from astro.runtime.observability import TauTurnObserver


def _json_events(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines() if line.strip().startswith("{")]


def test_conversation_log_fields_are_safe_by_default():
    prompt = "  Analyze this\nportfolio.  "

    fields = conversation_log_fields(
        prompt,
        include_excerpt=False,
        message_count=3,
    )

    assert fields == {
        "input_size_bytes": len(prompt.encode()),
        "message_count": 3,
    }


def test_conversation_log_fields_never_include_content_when_opted_in():
    prompt = f"  {'x' * 205}\n final"

    fields = conversation_log_fields(prompt, include_excerpt=True)

    assert fields == {"input_size_bytes": len(prompt.encode())}
    assert "prompt_excerpt" not in fields


def test_conversation_content_never_reaches_the_logger(capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)

    structlog.get_logger("astro.test").info(
        "test.prompt",
        **conversation_log_fields(
            "Use Bearer private-token to continue",
            include_excerpt=True,
        ),
    )

    event = _json_events(capsys.readouterr().out)[-1]
    assert event["input_size_bytes"] == len(b"Use Bearer private-token to continue")
    assert "private-token" not in json.dumps(event)


def test_structlog_json_matches_backend_fields_and_redacts(capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)
    bind_contextvars(request_id="request-1", session_uid="session-1")

    structlog.get_logger("astro.test").info(
        "test.native",
        message="Native event",
        authorization="Bearer private-token",
        nested={"credential_secret": "private-secret"},
    )
    logging.getLogger("foreign.test").warning(
        "Foreign bearer %s",
        "Bearer another-private-token",
    )
    clear_contextvars()

    events = _json_events(capsys.readouterr().out)
    native = next(event for event in events if event["event"] == "test.native")
    foreign = next(event for event in events if event["logger"] == "foreign.test")

    assert native["logger"] == "astro.test"
    assert native["level"] == "info"
    assert native["severity"] == "INFO"
    assert native["component"] == "astro.test"
    assert native["event_id"]
    assert native["runtime_instance_uid"]
    assert native["request_id"] == "request-1"
    assert native["session_uid"] == "session-1"
    assert native["authorization"] == "[REDACTED]"
    assert native["nested"] == {"credential_secret": "[REDACTED]"}
    assert "tests/unit/test_logging.py:" in str(native["source"])
    assert foreign["logger"] == "foreign.test"
    assert foreign["severity"] == "WARNING"
    assert "another-private-token" not in json.dumps(foreign)
    assert foreign["request_id"] == "request-1"


def test_google_cloud_trace_fields_are_derived_from_otel_context(
    capsys,
    monkeypatch,
):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    configure_logging("INFO", machine_sink=True, human_sink=False)

    structlog.get_logger("astro.trace").info(
        "trace.event",
        otelTraceID="abc123",
        otelSpanID="def456",
        otelTraceSampled=True,
    )

    event = _json_events(capsys.readouterr().out)[-1]
    assert event["logging.googleapis.com/trace"] == "projects/test-project/traces/abc123"
    assert event["logging.googleapis.com/spanId"] == "def456"
    assert event["logging.googleapis.com/trace_sampled"] is True
    assert "otelTraceID" not in event
    assert "otelSpanID" not in event
    assert "otelTraceSampled" not in event


def test_structlog_human_sink_uses_console_renderer_and_source(capsys):
    configure_logging("INFO", machine_sink=False, human_sink=True)

    structlog.get_logger("astro.test").error(
        "test.human",
        message="Readable human event",
        password="private-password",
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Readable human event" in captured.err
    assert "test.human" not in captured.err
    assert "message=" not in captured.err
    assert "[REDACTED]" in captured.err
    assert "private-password" not in captured.err
    assert "pathname=" in captured.err
    assert "source=" not in captured.err
    assert len(captured.err.splitlines()) == 1


async def test_request_context_emits_correlated_access_events(
    test_settings,
    asgi_client,
    capsys,
):
    app = create_app(test_settings)
    async with asgi_client(app, lifespan=True) as client:
        response = await client.get(
            "/health?private=value",
            headers={
                "X-Request-ID": "request-from-gateway",
                "X-User-UID": "user-1",
                "X-Coding-Agent-Service-UID": "service-1",
            },
        )

    events = _json_events(capsys.readouterr().out)
    started = next(event for event in events if event["event"] == "http.request.started")
    completed = next(event for event in events if event["event"] == "http.request.completed")

    assert response.headers["x-request-id"] == "request-from-gateway"
    assert completed["request_id"] == started["request_id"]
    assert completed["http_method"] == "GET"
    assert completed["route"] == "/health"
    assert completed["user_uid"] == "user-1"
    assert completed["coding_agent_service_uid"] == "service-1"
    assert completed["principal_type"] == "user"
    assert completed["status_class"] == "2xx"
    assert completed["outcome"] == "success"
    assert "private=value" not in json.dumps(completed)
    assert completed["status_code"] == 200
    assert isinstance(completed["duration_ms"], float)
    assert completed["response_size_bytes"] > 0


async def test_request_context_adds_endpoint_fields_to_completion_event(capsys, asgi_client):
    configure_logging("INFO", machine_sink=True, human_sink=False)

    async def endpoint(request: Request) -> PlainTextResponse:
        bind_request_log_fields(
            request.scope,
            session_uid="session-1",
            agent_session_uid="session-1",
        )
        return PlainTextResponse("ok")

    app = RequestContextMiddleware(Starlette(routes=[Route("/chat", endpoint, methods=["POST"])]))
    async with asgi_client(app) as client:
        response = await client.post("/chat")

    events = _json_events(capsys.readouterr().out)
    completed = next(event for event in events if event["event"] == "http.request.completed")

    assert response.status_code == 200
    assert completed["session_uid"] == "session-1"
    assert completed["agent_session_uid"] == "session-1"


async def test_request_logging_failure_does_not_change_response(monkeypatch, asgi_client):
    class FailingLogger:
        def info(self, event, **fields):
            raise OSError("sink unavailable")

        warning = info
        error = info

    monkeypatch.setattr(structlog, "get_logger", lambda *_args: FailingLogger())

    async def endpoint(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = RequestContextMiddleware(Starlette(routes=[Route("/health", endpoint)]))
    async with asgi_client(app) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.text == "ok"
    assert response.headers["x-request-id"]


async def test_human_request_completion_is_one_line_without_content(capsys, asgi_client):
    configure_logging("INFO", machine_sink=False, human_sink=True)

    async def endpoint(request: Request) -> PlainTextResponse:
        bind_request_log_fields(
            request.scope,
            session_uid="session-1",
            agent_session_uid="session-1",
        )
        return PlainTextResponse("ok")

    app = RequestContextMiddleware(Starlette(routes=[Route("/chat", endpoint, methods=["POST"])]))
    async with asgi_client(app) as client:
        response = await client.post("/chat")

    lines = capsys.readouterr().err.splitlines()
    completed = next(line for line in lines if "HTTP request completed" in line)

    assert response.status_code == 200
    assert "http_method=POST" in completed
    assert "route=__unmatched__" in completed
    assert "session_uid=session-1" in completed
    assert "agent_session_uid=session-1" in completed
    assert "Analyze this portfolio." not in completed
    assert "source=" not in completed


async def test_request_context_covers_stream_failure_and_context_cleanup(capsys, asgi_client):
    configure_logging("INFO", machine_sink=True, human_sink=False)

    async def stream(_request: Request) -> StreamingResponse:
        async def body():
            yield b"one"
            yield b"two"

        return StreamingResponse(body())

    async def fail(_request: Request) -> PlainTextResponse:
        raise ValueError("private exception value")

    app = RequestContextMiddleware(
        Starlette(
            routes=[
                Route("/stream", stream),
                Route("/fail", fail),
            ]
        )
    )
    async with asgi_client(app, raise_app_exceptions=False) as client:
        assert (await client.get("/stream", headers={"X-User-UID": "user-1"})).status_code == 200
        assert (await client.get("/fail")).status_code == 500

    events = _json_events(capsys.readouterr().out)
    terminals = [event for event in events if event["event"].startswith("http.request.")][1::2]
    assert terminals[0]["event"] == "http.request.completed"
    assert terminals[0]["is_streaming"] is True
    assert terminals[0]["response_size_bytes"] == 6
    assert terminals[0]["user_uid"] == "user-1"
    assert terminals[1]["event"] == "http.request.failed"
    assert terminals[1]["error_type"] == "ValueError"
    assert "user_uid" not in terminals[1]
    assert "private exception value" not in json.dumps(events)


def test_tau_turn_observer_logs_model_tool_and_handoff_without_payloads(capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)
    observer = TauTurnObserver(provider="openai", model="controlled-model")

    observer.observe(AstroRuntimeEvent(type="message_start"))
    observer.observe(AstroRuntimeEvent(type="text_delta", data={"text": "private output"}))
    observer.observe(
        AstroRuntimeEvent(
            type="tool_execution_start",
            data={
                "toolCallId": "tool-1",
                "toolName": "lookup",
                "arguments": {"password": "private input"},
            },
        )
    )
    observer.observe(
        AstroRuntimeEvent(
            type="tool_execution_end",
            data={
                "toolCallId": "tool-1",
                "toolName": "lookup",
                "result": {"secret": "private result"},
            },
        )
    )
    observer.observe(
        AstroRuntimeEvent(
            type="handoff_started",
            data={"source": "root", "target": "reviewer"},
        )
    )
    observer.observe(
        AstroRuntimeEvent(
            type="handoff_completed",
            data={"source": "root", "target": "reviewer"},
        )
    )
    observer.observe(
        AstroRuntimeEvent(
            type="message_end",
            data={
                "message": {
                    "usage": {
                        "input_tokens": 7,
                        "output_tokens": 3,
                        "total_tokens": 10,
                    }
                }
            },
        )
    )

    events = _json_events(capsys.readouterr().out)
    names = [event["event"] for event in events]
    assert "agent.model.started" in names
    assert "agent.model.completed" in names
    assert "agent.tool.started" in names
    assert "agent.tool.completed" in names
    assert "agent.handoff.started" in names
    assert "agent.handoff.completed" in names
    assert observer.terminal_fields()["input_tokens"] == 7
    assert observer.terminal_fields()["tool_calls"] == 1
    assert "private output" not in json.dumps(events)
    assert "private input" not in json.dumps(events)
    assert "private result" not in json.dumps(events)
