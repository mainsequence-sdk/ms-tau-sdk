import asyncio
import json
import logging
import stat
import subprocess
import sys

import pytest
import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route
from structlog.contextvars import bind_contextvars, clear_contextvars

from ms_tau_sdk.app import create_app
from ms_tau_sdk.logging import (
    RequestContextMiddleware,
    bind_request_log_fields,
    configure_logging,
    conversation_log_fields,
)
from ms_tau_sdk.runtime.events import TauRuntimeEvent
from ms_tau_sdk.runtime.observability import TauTurnObserver
from ms_tau_sdk.settings import TauSDKSettings


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

    structlog.get_logger("ms_tau_sdk.test").info(
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

    structlog.get_logger("ms_tau_sdk.test").info(
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

    assert native["logger"] == "ms_tau_sdk.test"
    assert native["level"] == "info"
    assert native["severity"] == "INFO"
    assert native["component"] == "ms_tau_sdk.test"
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

    structlog.get_logger("ms_tau_sdk.trace").info(
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

    structlog.get_logger("ms_tau_sdk.test").error(
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


def test_local_file_sink_is_structured_private_and_survives_reconfiguration(tmp_path, capsys):
    log_path = tmp_path / "state" / "logs" / "tau.jsonl"
    configure_logging("INFO", machine_sink=False, human_sink=True, file_path=log_path)
    bind_contextvars(request_id="local-request-1")

    structlog.get_logger("ms_tau_sdk.test").info(
        "local.first",
        authorization="Bearer private-token",
        nested={"credential_secret": "private-credential"},
    )
    try:
        raise ValueError("private exception message")
    except ValueError:
        structlog.get_logger("ms_tau_sdk.test").exception("local.failed")
        logging.getLogger("foreign.test").exception("Foreign failure")

    configure_logging("INFO", machine_sink=False, human_sink=True, file_path=log_path)
    structlog.get_logger("ms_tau_sdk.test").info("local.after_restart")
    clear_contextvars()

    events = _json_events(log_path.read_text(encoding="utf-8"))
    first = next(event for event in events if event["event"] == "local.first")
    failed = next(event for event in events if event["event"] == "local.failed")
    foreign = next(event for event in events if event["logger"] == "foreign.test")
    assert first["request_id"] == "local-request-1"
    assert first["authorization"] == "[REDACTED]"
    assert first["nested"] == {"credential_secret": "[REDACTED]"}
    assert failed["error_type"] == "ValueError"
    assert failed["exception_frames"]
    assert foreign["error_type"] == "ValueError"
    assert len([event for event in events if event["event"] == "local.after_restart"]) == 1
    assert "private exception message" not in log_path.read_text(encoding="utf-8")
    assert "private-token" not in log_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(log_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    assert "local.first" in capsys.readouterr().err


def test_local_app_always_creates_file_but_managed_app_does_not(tmp_path):
    local_settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        local_state_root=tmp_path / "state",
        local_mode=True,
        auth_mode="jwt",
        access_token="access-token",
        refresh_token="refresh-token",
        local_provider="openai",
        local_model="gpt-5.4",
    )
    create_app(local_settings)
    assert local_settings.local_log_path.is_file()

    managed_settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path / "state",
        local_state_root=tmp_path / "managed-state",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
    )
    create_app(managed_settings)
    assert not managed_settings.local_log_path.exists()


def test_local_file_sink_fails_startup_when_path_is_unwritable(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        local_state_root=blocked,
        local_mode=True,
        auth_mode="jwt",
        access_token="access-token",
        refresh_token="refresh-token",
        local_provider="openai",
        local_model="gpt-5.4",
    )
    with pytest.raises(OSError):
        create_app(settings)


def test_local_file_sink_rotates_valid_json_lines(tmp_path, monkeypatch):
    import ms_tau_sdk.logging as tau_logging

    monkeypatch.setattr(tau_logging, "LOCAL_LOG_MAX_BYTES", 1024)
    monkeypatch.setattr(tau_logging, "LOCAL_LOG_BACKUP_COUNT", 3)
    log_path = tmp_path / "logs" / "tau.jsonl"
    configure_logging("INFO", machine_sink=False, human_sink=False, file_path=log_path)
    for number in range(12):
        structlog.get_logger("ms_tau_sdk.test").info("local.rotate", sequence=number)

    log_files = [log_path, *sorted(log_path.parent.glob("tau.jsonl.[1-3]"))]
    assert len(log_files) > 1
    for file in log_files:
        assert _json_events(file.read_text(encoding="utf-8"))
        assert stat.S_IMODE(file.stat().st_mode) == 0o600


def test_local_file_sink_accepts_two_processes_without_corrupting_json(tmp_path):
    log_path = tmp_path / "logs" / "tau.jsonl"
    script = "\n".join(
        (
            "import sys",
            "from pathlib import Path",
            "import structlog",
            "from ms_tau_sdk.logging import configure_logging",
            "configure_logging('INFO', machine_sink=False, human_sink=False,",
            "                  file_path=Path(sys.argv[1]))",
            "for number in range(20):",
            "    structlog.get_logger('ms_tau_sdk.worker').info(",
            "        'local.concurrent', worker=sys.argv[2], sequence=number)",
        )
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(log_path), str(worker)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for worker in range(2)
    ]
    for process in processes:
        _stdout, stderr = process.communicate(timeout=8)
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")

    events = _json_events(log_path.read_text(encoding="utf-8"))
    assert len(events) == 40
    assert {(event["worker"], event["sequence"]) for event in events} == {
        (str(worker), number) for worker in range(2) for number in range(20)
    }


async def test_request_context_emits_correlated_access_events(
    test_settings,
    asgi_client,
    capsys,
):
    app = create_app(test_settings)
    async with asgi_client(app, lifespan=True) as client:
        response = await client.get(
            "/version?private=value",
            headers={
                "X-Request-ID": "request-from-gateway",
                "X-User-UID": "user-1",
                "X-Coding-Agent-Service-UID": "service-1",
                "X-Organization-Environment-UID": "environment-1",
            },
        )

    events = _json_events(capsys.readouterr().out)
    completed = next(event for event in events if event["event"] == "http.request.completed")

    assert response.headers["x-request-id"] == "request-from-gateway"
    assert not any(event["event"] == "http.request.started" for event in events)
    assert completed["request_id"] == "request-from-gateway"
    assert completed["http_method"] == "GET"
    assert completed["route"] == "/version"
    assert completed["user_uid"] == "user-1"
    assert completed["coding_agent_service_uid"] == "service-1"
    assert completed["organization_environment_uid"] == "environment-1"
    assert "project_environment_uid" not in completed
    assert completed["principal_type"] == "user"
    assert completed["status_class"] == "2xx"
    assert completed["outcome"] == "success"
    assert "private=value" not in json.dumps(completed)
    assert completed["status_code"] == 200
    assert isinstance(completed["duration_ms"], float)
    assert completed["response_size_bytes"] > 0
    assert "filename" not in completed
    assert "lineno" not in completed
    assert "func_name" not in completed
    assert "source" not in completed


def test_environment_context_uses_only_canonical_reserved_field(capsys, monkeypatch):
    monkeypatch.setenv(
        "MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID",
        "environment-trusted",
    )
    configure_logging("INFO", machine_sink=True, human_sink=False)

    structlog.get_logger("ms_tau_sdk.project").info(
        "project.domain.event",
        organization_environment_uid="environment-forged",
    )

    event = _json_events(capsys.readouterr().out)[-1]
    assert event["organization_environment_uid"] == "environment-trusted"


def test_environment_context_uses_only_code_repository_uid(capsys, monkeypatch):
    monkeypatch.setenv("MAINSEQUENCE_CODE_REPOSITORY_UID", "repository-1")
    configure_logging("INFO", machine_sink=True, human_sink=False)

    structlog.get_logger("ms_tau_sdk.code_repository").info("code_repository.domain.event")

    event = _json_events(capsys.readouterr().out)[-1]
    assert event["code_repository_uid"] == "repository-1"


async def test_successful_platform_probes_emit_no_request_logs(
    test_settings,
    asgi_client,
    capsys,
):
    app = create_app(test_settings)
    async with asgi_client(app, lifespan=True) as client:
        for _ in range(3):
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/ready")).status_code == 200

    events = _json_events(capsys.readouterr().out)
    assert not any(event["event"].startswith("http.request.") for event in events)
    assert not any(event["event"].startswith("runtime.probe.") for event in events)


async def test_probe_failures_are_rate_limited_and_recovery_is_logged(
    capsys,
    asgi_client,
    monkeypatch,
):
    monkeypatch.setenv("MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID", "environment-probe")
    configure_logging("INFO", machine_sink=True, human_sink=False)
    state = {"healthy": False}

    async def health(_request: Request) -> PlainTextResponse:
        return PlainTextResponse(
            "ok" if state["healthy"] else "failed",
            status_code=200 if state["healthy"] else 503,
        )

    app = RequestContextMiddleware(Starlette(routes=[Route("/health", health)]))
    async with asgi_client(app) as client:
        assert (await client.get("/health")).status_code == 503
        assert (await client.get("/health")).status_code == 503
        state["healthy"] = True
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/health")).status_code == 200

    events = _json_events(capsys.readouterr().out)
    probe_events = [event for event in events if event["event"].startswith("runtime.probe.")]
    assert [event["event"] for event in probe_events] == [
        "runtime.probe.failed",
        "runtime.probe.recovered",
    ]
    assert all(
        event["organization_environment_uid"] == "environment-probe" for event in probe_events
    )
    assert not any(event["event"].startswith("http.request.") for event in events)


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

    app = RequestContextMiddleware(Starlette(routes=[Route("/application-health", endpoint)]))
    async with asgi_client(app) as client:
        response = await client.get("/application-health")

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
    terminals = [event for event in events if event["event"].startswith("http.request.")]
    assert terminals[0]["event"] == "http.request.completed"
    assert terminals[0]["is_streaming"] is True
    assert terminals[0]["response_size_bytes"] == 6
    assert terminals[0]["user_uid"] == "user-1"
    assert terminals[1]["event"] == "http.request.failed"
    assert terminals[1]["error_type"] == "ValueError"
    assert "user_uid" not in terminals[1]
    assert "private exception value" not in json.dumps(events)


async def test_cancelled_and_disconnected_requests_have_one_safe_terminal(capsys, monkeypatch):
    monkeypatch.setenv("MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID", "environment-1")
    configure_logging("INFO", machine_sink=True, human_sink=False)

    async def cancelled(scope, receive, send):
        raise asyncio.CancelledError

    async def disconnected(scope, receive, send):
        assert (await receive())["type"] == "http.disconnect"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    def scope(path):
        return {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "http_version": "1.1",
        }

    async def receive_disconnect():
        return {"type": "http.disconnect"}

    async def send(_message):
        return None

    with pytest.raises(asyncio.CancelledError):
        await RequestContextMiddleware(cancelled)(scope("/cancelled"), receive_disconnect, send)
    await RequestContextMiddleware(disconnected)(scope("/disconnected"), receive_disconnect, send)

    terminals = [
        event
        for event in _json_events(capsys.readouterr().out)
        if event["event"].startswith("http.request.")
    ]
    assert [event["event"] for event in terminals] == [
        "http.request.cancelled",
        "http.request.completed",
    ]
    assert [event["outcome"] for event in terminals] == ["cancelled", "disconnected"]
    assert all(event["organization_environment_uid"] == "environment-1" for event in terminals)


def test_tau_turn_observer_logs_model_tool_and_handoff_without_payloads(capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)
    observer = TauTurnObserver(provider="openai", model="controlled-model")

    observer.observe(TauRuntimeEvent(type="message_start"))
    observer.observe(TauRuntimeEvent(type="text_delta", data={"text": "private output"}))
    observer.observe(
        TauRuntimeEvent(
            type="tool_execution_start",
            data={
                "toolCallId": "tool-1",
                "toolName": "lookup",
                "arguments": {"password": "private input"},
            },
        )
    )
    observer.observe(
        TauRuntimeEvent(
            type="tool_execution_end",
            data={
                "toolCallId": "tool-1",
                "toolName": "lookup",
                "result": {"secret": "private result"},
            },
        )
    )
    observer.observe(
        TauRuntimeEvent(
            type="handoff_started",
            data={"source": "root", "target": "reviewer"},
        )
    )
    observer.observe(
        TauRuntimeEvent(
            type="handoff_completed",
            data={"source": "root", "target": "reviewer"},
        )
    )
    observer.observe(
        TauRuntimeEvent(
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


def test_tau_model_rate_limit_retry_preserves_safe_correlated_attempts(capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)
    observer = TauTurnObserver(provider="openai", model="controlled-model")

    observer.observe(TauRuntimeEvent(type="model_start"))
    observer.observe(
        TauRuntimeEvent(
            type="model_error",
            data={
                "error_type": "ProviderRateLimitError",
                "status_code": 429,
                "provider_request_id": "provider-request-1",
                "message": "private provider response",
                "prompt": "private prompt",
            },
        )
    )
    observer.observe(TauRuntimeEvent(type="model_start"))
    observer.observe(
        TauRuntimeEvent(
            type="model_end",
            data={"provider_request_id": "provider-request-2"},
        )
    )

    events = _json_events(capsys.readouterr().out)
    model_events = [event for event in events if event["event"].startswith("agent.model.")]
    assert [event["event"] for event in model_events] == [
        "agent.model.started",
        "agent.model.failed",
        "agent.model.started",
        "agent.model.completed",
    ]
    assert [event["model_attempt"] for event in model_events] == [1, 1, 2, 2]
    assert model_events[0]["model_call_uid"] == model_events[1]["model_call_uid"]
    assert model_events[2]["model_call_uid"] == model_events[3]["model_call_uid"]
    assert model_events[0]["model_call_uid"] != model_events[2]["model_call_uid"]
    assert model_events[1]["rate_limited"] is True
    assert model_events[1]["retryable"] is True
    assert model_events[1]["provider_request_id"] == "provider-request-1"
    assert "private provider response" not in json.dumps(model_events)
    assert "private prompt" not in json.dumps(model_events)


def test_tau_terminal_provider_failure_is_observed_as_failed_without_payloads(capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)
    observer = TauTurnObserver(provider="openrouter", model="controlled-model")

    observer.observe(TauRuntimeEvent(type="message_start"))
    observer.observe(
        TauRuntimeEvent(
            type="message_end",
            data={
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": "private provider response",
                    "diagnostics": [
                        {
                            "type": "provider_error",
                            "details": {
                                "status_code": 402,
                                "body": "private raw response body",
                            },
                        }
                    ],
                }
            },
        )
    )

    events = _json_events(capsys.readouterr().out)
    model_events = [event for event in events if event["event"].startswith("agent.model.")]
    assert [event["event"] for event in model_events] == [
        "agent.model.started",
        "agent.model.failed",
    ]
    assert model_events[1]["model_error_type"] == "ProviderError"
    assert model_events[1]["retryable"] is False
    assert observer.terminal_failure is not None
    assert observer.terminal_failure.message == "private provider response"
    assert "private provider response" not in json.dumps(model_events)
    assert "private raw response body" not in json.dumps(model_events)


def test_tau_tool_timeout_retry_preserves_safe_canonical_approval_outcome(capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)
    observer = TauTurnObserver(provider="openai", model="controlled-model")
    metadata = {
        "toolName": "create_ticket",
        "tool_category": "mcp",
        "target_system": "jira",
        "side_effect_class": "write",
        "approval_required": True,
        "approval_outcome": "approved",
    }

    observer.observe(
        TauRuntimeEvent(
            type="tool_execution_start",
            data={**metadata, "toolCallId": "first", "arguments": {"token": "private token"}},
        )
    )
    observer.observe(
        TauRuntimeEvent(
            type="tool_timeout",
            data={"toolCallId": "first", "message": "private provider response"},
        )
    )
    observer.observe(
        TauRuntimeEvent(type="tool_execution_start", data={**metadata, "toolCallId": "second"})
    )
    observer.observe(
        TauRuntimeEvent(
            type="tool_execution_end",
            data={"toolCallId": "second", "result": {"secret": "private tool result"}},
        )
    )

    events = [
        event
        for event in _json_events(capsys.readouterr().out)
        if event["event"].startswith("agent.tool.")
    ]
    assert [event["tool_attempt"] for event in events] == [1, 1, 2, 2]
    assert events[1]["tool_error_type"] == "TimeoutError"
    assert events[1]["retryable"] is True
    assert all(event["target_system"] == "jira" for event in events)
    assert all(event["approval_outcome"] == "approved" for event in events)
    assert "private token" not in json.dumps(events)
    assert "private provider response" not in json.dumps(events)
    assert "private tool result" not in json.dumps(events)


def test_tau_handoff_carries_session_correlation_and_allowlisted_reason(capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)
    observer = TauTurnObserver(provider="openai", model="controlled-model")

    observer.observe(
        TauRuntimeEvent(
            type="handoff_started",
            data={
                "handoff_uid": "handoff-1",
                "source": "supervisor",
                "target": "reviewer",
                "parent_session_uid": "session-parent",
                "child_session_uid": "session-child",
                "reason_code": "specialist",
                "prompt": "private handoff instructions",
            },
        )
    )
    observer.observe(TauRuntimeEvent(type="handoff_failed", data={"handoff_uid": "handoff-1"}))

    events = _json_events(capsys.readouterr().out)
    handoffs = [event for event in events if event["event"].startswith("agent.handoff.")]
    assert [event["event"] for event in handoffs] == [
        "agent.handoff.started",
        "agent.handoff.failed",
    ]
    for handoff in handoffs:
        assert handoff["handoff_uid"] == "handoff-1"
        assert handoff["parent_agent_session_uid"] == "session-parent"
        assert handoff["child_agent_session_uid"] == "session-child"
        assert handoff["handoff_reason_code"] == "specialist"
    assert handoffs[1]["outcome"] == "failed"
    assert "private handoff instructions" not in json.dumps(handoffs)


async def test_request_context_binds_gateway_caller_identity(test_settings, asgi_client, capsys):
    app = create_app(test_settings)
    async with asgi_client(app, lifespan=True) as client:
        await client.get(
            "/version",
            headers={
                "X-User-UID": "user-1",
                "X-Caller-Kind": "agent",
                "X-Caller-Agent-UID": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
            },
        )
    events = _json_events(capsys.readouterr().out)
    completed = next(event for event in events if event["event"] == "http.request.completed")
    assert completed["caller_kind"] == "agent"
    assert completed["caller_agent_uid"] == "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
