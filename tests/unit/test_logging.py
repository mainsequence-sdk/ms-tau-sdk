import hashlib
import json
import logging

import structlog
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from structlog.contextvars import bind_contextvars, clear_contextvars

from astro.app import create_app
from astro.logging import (
    RequestContextMiddleware,
    configure_logging,
    conversation_log_fields,
)
from astro.settings import Settings


def _json_events(output: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in output.splitlines()
        if line.strip().startswith("{")
    ]


def test_conversation_log_fields_are_safe_by_default():
    prompt = "  Analyze this\nportfolio.  "

    fields = conversation_log_fields(
        prompt,
        include_excerpt=False,
        message_count=3,
    )

    assert fields == {
        "prompt_chars": len(prompt),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "message_count": 3,
    }


def test_conversation_log_fields_normalize_and_truncate_excerpt():
    prompt = f"  {'x' * 205}\n final"

    fields = conversation_log_fields(prompt, include_excerpt=True)

    assert fields["prompt_excerpt"] == f"{'x' * 197}..."
    assert len(str(fields["prompt_excerpt"])) == 200


def test_conversation_excerpt_passes_through_secret_redaction(capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)

    structlog.get_logger("astro.test").info(
        "test.prompt",
        **conversation_log_fields(
            "Use Bearer private-token to continue",
            include_excerpt=True,
        ),
    )

    event = _json_events(capsys.readouterr().out)[-1]
    assert event["prompt_excerpt"] == "Use Bearer [REDACTED] to continue"
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
    foreign = next(
        event
        for event in events
        if event["logger"] == "foreign.test"
    )

    assert native["logger"] == "astro.test"
    assert native["level"] == "info"
    assert native["severity"] == "INFO"
    assert native["component"] == "astro.test"
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
    assert (
        event["logging.googleapis.com/trace"]
        == "projects/test-project/traces/abc123"
    )
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


def test_request_context_emits_correlated_access_events(tmp_path, capsys):
    app = create_app(
        Settings(
            _env_file=None,
            backend_url="http://backend:8000",
            runtime_credential_id="credential-id",
            runtime_credential_secret="credential-secret",
            project_root=tmp_path,
            log_machine_sink=True,
            log_human_sink=False,
        )
    )

    with TestClient(app) as client:
        response = client.get("/health")

    events = _json_events(capsys.readouterr().out)
    started = next(
        event for event in events if event["event"] == "http.request.started"
    )
    completed = next(
        event for event in events if event["event"] == "http.request.completed"
    )

    assert response.headers["x-request-id"] == started["request_id"]
    assert completed["request_id"] == started["request_id"]
    assert completed["http_method"] == "GET"
    assert completed["route"] == "/health"
    assert completed["http_path"] == "/health"
    assert completed["status_code"] == 200
    assert isinstance(completed["duration_ms"], float)
    assert completed["response_size_bytes"] > 0


def test_request_context_adds_endpoint_fields_to_completion_event(capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)

    async def endpoint(request: Request) -> PlainTextResponse:
        request.state.request_log_fields = {
            "session_uid": "session-1",
            "prompt_excerpt": "Analyze this portfolio.",
        }
        return PlainTextResponse("ok")

    app = RequestContextMiddleware(
        Starlette(routes=[Route("/chat", endpoint, methods=["POST"])])
    )
    with TestClient(app) as client:
        response = client.post("/chat")

    events = _json_events(capsys.readouterr().out)
    completed = next(
        event for event in events if event["event"] == "http.request.completed"
    )

    assert response.status_code == 200
    assert completed["session_uid"] == "session-1"
    assert completed["prompt_excerpt"] == "Analyze this portfolio."


def test_human_request_completion_is_one_line_with_prompt_excerpt(capsys):
    configure_logging("INFO", machine_sink=False, human_sink=True)

    async def endpoint(request: Request) -> PlainTextResponse:
        request.state.request_log_fields = {
            "session_uid": "session-1",
            "prompt_excerpt": "Analyze this portfolio.",
        }
        return PlainTextResponse("ok")

    app = RequestContextMiddleware(
        Starlette(routes=[Route("/chat", endpoint, methods=["POST"])])
    )
    with TestClient(app) as client:
        response = client.post("/chat")

    lines = capsys.readouterr().err.splitlines()
    completed = next(line for line in lines if "HTTP request completed" in line)

    assert response.status_code == 200
    assert "http_method=POST" in completed
    assert "http_path=/chat" in completed
    assert "session_uid=session-1" in completed
    assert "prompt_excerpt='Analyze this portfolio.'" in completed
    assert "source=" not in completed
