"""Caller context: the W3C ``traceparent`` and ``baggage`` a request or Task carries.

The runtime reads the context next to the verified caller, makes it OpenTelemetry's current
context, carries it on every call it makes, and records its ``mainsequence.`` keys on log lines.
It gives no key a meaning. The keys belong to an invented application, ``portal``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import httpx2
import structlog
from fastapi import Request
from opentelemetry import baggage, trace
from opentelemetry.context import Context

from ms_tau_sdk import create_app
from ms_tau_sdk.backend.client import MainSequenceClient, answer_without_request_credentials
from ms_tau_sdk.backend.mcp import MCPConnectionClient
from ms_tau_sdk.logging import configure_logging
from ms_tau_sdk.protocols.caller_context import (
    caller_context_scope,
    outbound_headers,
    recorded_baggage,
    request_context,
)
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings

ASSERTION_HEADER = "X-MainSequence-Caller-Assertion"
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
TRACE_ID = 0x4BF92F3577B34DA6A3CE929D0E0E4736
BAGGAGE = "mainsequence.portal.user=u-81,mainsequence.portal.request=r-7,region=emea"
CARRIED = {
    "mainsequence.portal.user": "u-81",
    "mainsequence.portal.request": "r-7",
    "region": "emea",
}
RECORDED = {"mainsequence.portal.user": "u-81", "mainsequence.portal.request": "r-7"}


def _headers(**values: str) -> list[tuple[bytes, bytes]]:
    return [(name.encode("latin-1"), value.encode("latin-1")) for name, value in values.items()]


def _context(**values: str) -> Context:
    context = Context()
    for key, value in values.items():
        context = baggage.set_baggage(key, value, context=context)
    return context


# --- Reading a request's context --------------------------------------------------------------


def test_a_request_carries_its_baggage_and_trace_parent():
    context = request_context(_headers(traceparent=TRACEPARENT, baggage=BAGGAGE))

    assert baggage.get_all(context=context) == CARRIED
    span = trace.get_current_span(context).get_span_context()
    assert span.trace_id == TRACE_ID
    assert span.is_remote


def test_properties_are_dropped_and_values_are_kept_as_sent():
    context = request_context(
        _headers(baggage="mainsequence.portal.user=u%3B81;owner=portal,mainsequence.portal.tier=2")
    )

    # An encoded ';' belongs to the value; the unencoded one starts a property, which is dropped.
    assert baggage.get_all(context=context) == {
        "mainsequence.portal.user": "u;81",
        "mainsequence.portal.tier": "2",
    }


def test_repeated_baggage_headers_are_read_together():
    context = request_context(
        [(b"baggage", b"mainsequence.portal.user=u-81"), (b"Baggage", b"mainsequence.portal.r=7")]
    )

    assert baggage.get_all(context=context) == {
        "mainsequence.portal.user": "u-81",
        "mainsequence.portal.r": "7",
    }


def test_the_parsers_limits_apply_and_never_fail_the_request(caplog):
    oversized_header = "mainsequence.portal.user=" + "u" * 8200
    oversized_member = f"mainsequence.portal.note={'n' * 4100},mainsequence.portal.user=u-81"
    many = ",".join(f"mainsequence.portal.k{index}={index}" for index in range(200))

    with caplog.at_level(logging.DEBUG):
        whole = request_context(_headers(baggage=oversized_header))
        member = request_context(_headers(baggage=oversized_member))
        counted = request_context(_headers(baggage=many))
        malformed = request_context(_headers(baggage="mainsequence.portal.user,=,;;,"))

    assert baggage.get_all(context=whole) == {}
    assert baggage.get_all(context=member) == {"mainsequence.portal.user": "u-81"}
    assert len(baggage.get_all(context=counted)) == 180
    assert baggage.get_all(context=malformed) == {}
    # The parser's warnings quote the header; none is logged.
    assert not [record for record in caplog.records if "mainsequence.portal" in record.message]


def test_a_request_without_baggage_has_none_whatever_the_process_carries():
    with caller_context_scope(_context(**{"mainsequence.portal.user": "someone-else"})):
        context = request_context(_headers(traceparent=TRACEPARENT))

    assert baggage.get_all(context=context) == {}
    assert trace.get_current_span(context).get_span_context().trace_id == TRACE_ID


def test_an_invalid_trace_parent_is_ignored():
    context = request_context(_headers(traceparent="not-a-trace-parent", baggage=BAGGAGE))

    assert not trace.get_current_span(context).get_span_context().is_valid
    assert baggage.get_all(context=context) == CARRIED


# --- Passing it on ----------------------------------------------------------------------------


def test_outbound_headers_carry_the_current_context():
    assert outbound_headers() == {}

    with caller_context_scope(request_context(_headers(traceparent=TRACEPARENT, baggage=BAGGAGE))):
        headers = outbound_headers()
        own = outbound_headers({"Baggage": "mainsequence.portal.user=mine"})

    assert headers["traceparent"] == TRACEPARENT
    assert baggage.get_all(context=request_context(_headers(**headers))) == CARRIED
    # A header the call sets itself, in any case, wins.
    assert own == {"traceparent": TRACEPARENT}


def test_only_mainsequence_keys_are_recorded():
    context = _context(**CARRIED)

    assert recorded_baggage(context) == RECORDED
    with caller_context_scope(context):
        assert recorded_baggage() == RECORDED
    assert recorded_baggage() == {}


async def test_platform_calls_carry_the_caller_context(runtime_identity_token_file):
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path.startswith("/api/v1/users/"):
            return httpx.Response(200, json={"uid": "user-1"})
        return httpx.Response(200, json={"ok": True})

    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="credential-id",
        runtime_identity_token_file=runtime_identity_token_file,
    )
    auth = AsyncMock()
    auth.headers.return_value = {"Authorization": "Bearer runtime"}
    auth.bind_client = Mock()
    http = httpx.AsyncClient(base_url=settings.backend_url, transport=httpx.MockTransport(handle))
    client = MainSequenceClient(settings, auth, client=http)

    await client.get_user("user-1")
    with caller_context_scope(request_context(_headers(traceparent=TRACEPARENT, baggage=BAGGAGE))):
        await client.get_user("user-1")
        answer = await client.platform_request(
            "GET",
            "/api/v1/data-nodes/",
            proof=None,
            headers={"baggage": "mainsequence.portal.user=chosen-by-the-tool"},
        )
    await http.aclose()

    without, with_context, own = sent
    assert "baggage" not in without.headers and "traceparent" not in without.headers
    assert with_context.headers["traceparent"] == TRACEPARENT
    assert baggage.get_all(context=request_context(with_context.headers.raw)) == CARRIED
    assert own.headers.get_list("baggage") == ["mainsequence.portal.user=chosen-by-the-tool"]
    assert own.headers["traceparent"] == TRACEPARENT
    assert answer_without_request_credentials(answer).request.headers.get("baggage") is None


# --- Request identity -------------------------------------------------------------------------


def _log_events(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


async def test_a_verified_request_runs_in_its_caller_context(
    served_platform_keys, tmp_path, asgi_client, capsys
):
    app = create_app(served_platform_keys.hosted_settings(tmp_path))
    seen: dict[str, Any] = {}

    @app.get("/context")
    async def context(request: Request) -> dict[str, str]:
        structlog.get_logger("portal.tool").info("portal.tool.ran")
        seen["user_uid"] = request.state.user_uid
        seen["trace_id"] = trace.get_current_span().get_span_context().trace_id
        return {str(key): str(value) for key, value in baggage.get_all().items()}

    async with asgi_client(app) as http:
        response = await http.get(
            "/context",
            headers={
                ASSERTION_HEADER: served_platform_keys.caller_assertion(),
                "traceparent": TRACEPARENT,
                "baggage": BAGGAGE,
            },
        )

    assert response.status_code == 200
    # Agent code reads every key with OpenTelemetry's own API, next to the verified caller.
    assert response.json() == CARRIED
    assert seen == {"user_uid": served_platform_keys.user_uid, "trace_id": TRACE_ID}
    assert baggage.get_all() == {}
    events = _log_events(capsys.readouterr().out)
    ran = next(event for event in events if event["event"] == "portal.tool.ran")
    completed = next(event for event in events if event["event"] == "http.request.completed")
    # Log lines record only the mainsequence. keys, and never the raw header.
    assert ran["baggage"] == RECORDED
    assert completed["baggage"] == RECORDED
    assert "region" not in json.dumps(events)


async def test_a_rejected_request_has_no_caller_context(
    served_platform_keys, tmp_path, asgi_client, capsys
):
    app = create_app(served_platform_keys.hosted_settings(tmp_path))
    reached: list[bool] = []

    @app.get("/context")
    async def context() -> dict[str, str]:
        reached.append(True)
        return {}

    async with asgi_client(app) as http:
        response = await http.get("/context", headers={"baggage": BAGGAGE})

    assert response.status_code == 401
    assert reached == []
    events = _log_events(capsys.readouterr().out)
    assert all("baggage" not in event for event in events)


async def test_outside_hosting_requests_carry_no_caller_context(test_settings, asgi_client):
    app = create_app(test_settings)

    @app.get("/context")
    async def context() -> dict[str, str]:
        return {str(key): str(value) for key, value in baggage.get_all().items()}

    async with asgi_client(app) as http:
        response = await http.get("/context", headers={"baggage": BAGGAGE})

    assert response.status_code == 200
    assert response.json() == {}


def test_log_lines_record_the_current_caller_context(capsys):
    configure_logging("INFO")
    logger = structlog.get_logger("portal.tool")

    logger.info("portal.before")
    with caller_context_scope(_context(**CARRIED)):
        logger.info("portal.during")
        logging.getLogger("portal.stdlib").warning("portal.stdlib")
    logger.info("portal.after")

    events = {event["event"]: event for event in _log_events(capsys.readouterr().out)}
    assert "baggage" not in events["portal.before"]
    assert events["portal.during"]["baggage"] == RECORDED
    assert events["portal.stdlib"]["baggage"] == RECORDED
    assert "baggage" not in events["portal.after"]


# --- MCP calls, A2A sends included -------------------------------------------------------------


async def test_mcp_calls_carry_the_callers_context_and_the_connection_none(monkeypatch):
    real_client = httpx2.AsyncClient
    received: list[tuple[str, httpx2.Headers]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        message = json.loads(request.content)
        method = message.get("method", "")
        received.append((method, request.headers))
        if "id" not in message:
            return httpx2.Response(202)
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "1"},
            }
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "sent"}], "isError": False}
        else:
            result = {"tools": [{"name": "a2a.send_message", "inputSchema": {"type": "object"}}]}
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx2.MockTransport(handler), **kwargs),
    )
    with caller_context_scope(_context(**{"mainsequence.portal.user": "opener"})):
        # The connection outlives the request that opened it; its own requests carry no context.
        client = await MCPConnectionClient(
            name="mainsequence",
            display_name="Main Sequence",
            url="http://backend.test/mcp",
            auth=None,
            timeout=httpx2.Timeout(5.0),
            read_timeout_seconds=5.0,
            read_concurrency=2,
        ).open()
    try:
        await client.call_tool("a2a.send_message", {})
        with caller_context_scope(
            request_context(_headers(traceparent=TRACEPARENT, baggage=BAGGAGE))
        ):
            await client.call_tool("a2a.send_message", {})
    finally:
        await client.aclose()

    opening = [headers for method, headers in received if method != "tools/call"]
    calls = [headers for method, headers in received if method == "tools/call"]
    assert opening and all("baggage" not in headers for headers in opening)
    without, with_context = calls
    assert "baggage" not in without and "traceparent" not in without
    assert with_context["traceparent"] == TRACEPARENT
    assert baggage.get_all(context=request_context(with_context.raw)) == CARRIED


# --- Task executions and the work they start ---------------------------------------------------


def _manager(tmp_path, **settings: Any) -> SessionRuntimeManager:
    return SessionRuntimeManager(
        settings=TauSDKSettings(
            _env_file=None,
            runtime_credential_id="credential-id",
            workspace=tmp_path,
            startup_dependencies_enabled=False,
            **settings,
        ),
        backend=AsyncMock(),
        providers=Mock(),
    )


async def test_a_task_execution_runs_in_the_tasks_caller_context(tmp_path):
    manager = _manager(tmp_path)
    task_context = request_context(_headers(traceparent=TRACEPARENT, baggage=BAGGAGE))

    async def observe() -> dict[str, object]:
        return dict(baggage.get_all())

    with caller_context_scope(_context(**{"mainsequence.portal.user": "the-signal"})):
        given, _ = manager.create_a2a_task_execution(
            "task-1", observe(), name="given", caller_context=task_context
        )
        empty, _ = manager.create_a2a_task_execution(
            "task-2", observe(), name="empty", caller_context=request_context(())
        )
        inherited, _ = manager.create_a2a_task_execution("task-3", observe(), name="inherited")

    assert await given == CARRIED
    assert await empty == {}
    assert await inherited == {"mainsequence.portal.user": "the-signal"}


async def test_lease_renewals_carry_no_callers_context(tmp_path):
    manager = _manager(
        tmp_path,
        runtime_lease_renew_interval_seconds=1,
        runtime_lease_ttl_seconds=30,
    )
    runtime = SimpleNamespace(
        session_uid="session-1",
        evicting=False,
        storage=SimpleNamespace(lease_token="lease", replace_lease_token=Mock()),
    )
    renewed_with: list[dict[str, object]] = []

    async def renew(*_args: object) -> SimpleNamespace:
        renewed_with.append(dict(baggage.get_all()))
        runtime.evicting = True
        return SimpleNamespace(
            lease_token="lease-2",
            cancel_requested=False,
            checkpoint_version=1,
            lease_expires_at=None,
        )

    manager.backend.renew_runtime_lease.side_effect = renew  # type: ignore[attr-defined]
    with caller_context_scope(_context(**RECORDED)):
        renewal = asyncio.create_task(manager._renew_lease(runtime))  # type: ignore[arg-type]
    await asyncio.wait_for(renewal, timeout=5)

    assert renewed_with == [{}]
