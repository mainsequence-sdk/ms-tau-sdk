"""Request identity of the application (issues #59 and #60).

A hosted runtime admits a request only with the platform's signed assertion of the kind its route
requires, declares request identity for the platform launcher, and lets only a session's owner or
an Organization admin address that session. The platform's key set is served by the
`served_platform_keys` fixture; nothing leaves the process.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from pydantic import ValidationError

from ms_tau_sdk import create_app
from ms_tau_sdk.api.a2a import RESPONSE_KIND_EXTENSION_URI, REST_BASE
from ms_tau_sdk.api.dependencies import backend, runtime_manager, settings
from ms_tau_sdk.api.request_identity import (
    SESSION_ACCESS_DENIED_DETAIL,
    RequestIdentityMiddleware,
    current_caller,
)
from ms_tau_sdk.backend.models import (
    AgentCardEnvelope,
    AgentSession,
    AgentTask,
    AgentTaskCreateResult,
    AgentTaskSnapshot,
    RuntimeState,
)
from ms_tau_sdk.errors import ConfigurationError
from ms_tau_sdk.runtime.events import TauRuntimeEvent
from ms_tau_sdk.settings import TauSDKSettings

ASSERTION_HEADER = "X-MainSequence-Caller-Assertion"
# The User the `platform_keys` fixture signs caller assertions for unless told otherwise.
OWNER_UID = "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d"
OTHER_UID = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"
ADMIN_UID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
OWNED_SESSION = "11111111-aaaa-4bbb-8ccc-222222222222"
FOREIGN_SESSION = "33333333-dddd-4eee-8fff-444444444444"
AGENT_CALLER_HEADERS = {
    "X-Caller-Kind": "agent",
    "X-Caller-Agent-UID": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "X-Caller-Coding-Agent-Service-UID": "f0e1d2c3-b4a5-4968-8776-655443322110",
}


def _session(uid: str, owner: str) -> AgentSession:
    return AgentSession(
        uid=uid,
        agent_uid="agent-1",
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.4.2",
        llm_provider="openai",
        llm_model="gpt-5.4",
        created_by_user_uid=owner,
    )


def _task(task_id: str, session_uid: str, status: str = "submitted") -> AgentTask:
    return AgentTask(
        uid=f"backend-{task_id}",
        task_id=task_id,
        context_id=session_uid,
        agent_uid="agent-1",
        agent_session_uid=session_uid,
        status=status,
        status_timestamp=datetime(2026, 10, 5, tzinfo=UTC),
    )


class _Manager:
    holder_id = "holder-1"

    def __init__(self, config: TauSDKSettings, client: AsyncMock) -> None:
        self.settings = config
        self.backend = client
        self.prompted: list[str] = []
        self.provenances: list[object] = []
        self.cancelled: list[str] = []

    async def prompt(self, session_uid: str, _prompt: str, *, provenance=None, **_options):
        self.prompted.append(session_uid)
        self.provenances.append(provenance)
        yield TauRuntimeEvent(
            type="message_end",
            data={
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Answer."}],
                    "stopReason": "stop",
                }
            },
        )
        yield TauRuntimeEvent(type="agent_settled")

    async def cancel(self, session_uid: str) -> bool:
        self.cancelled.append(session_uid)
        return True

    def mark_response_delivered(self, _session_uid: str) -> bool:
        return True

    def session_turn_active(self, _session_uid: str) -> bool:
        return False

    async def persist_platform_event(self, *_args: object, **_kwargs: object) -> bool:
        return False


def _client(config: TauSDKSettings) -> AsyncMock:
    owners = {OWNED_SESSION: OWNER_UID, FOREIGN_SESSION: OTHER_UID}
    tasks = {
        "task-owned": _task("task-owned", OWNED_SESSION),
        "task-foreign": _task("task-foreign", FOREIGN_SESSION),
    }
    client = AsyncMock()
    client.settings = config

    async def get_session(session_uid: str) -> AgentSession:
        return _session(session_uid, owners[session_uid])

    async def get_task_by_protocol_id(task_id: str, **_kwargs: object) -> AgentTask:
        return tasks[task_id]

    async def get_task_snapshot_by_protocol_id(task_id: str) -> AgentTaskSnapshot:
        terminal = tasks[task_id].model_copy(update={"status": "completed"})
        return AgentTaskSnapshot(task=terminal, event_cursor=1)

    async def list_tasks(*, context_id: str, **_kwargs: object) -> list[AgentTask]:
        return [task for task in tasks.values() if context_id in {"", task.context_id}]

    async def create_task(payload: dict[str, Any]) -> AgentTaskCreateResult:
        # Every create replays a finished Task: the existing one with that ID, or a new one in
        # the session the request named.
        existing = tasks.get(payload["task_id"])
        replayed = existing or _task(payload["task_id"], payload["agent_session_uid"])
        return AgentTaskCreateResult(
            task=replayed.model_copy(update={"status": "completed"}),
            created=False,
        )

    client.get_session.side_effect = get_session
    client.get_task_by_protocol_id.side_effect = get_task_by_protocol_id
    client.get_task_snapshot_by_protocol_id.side_effect = get_task_snapshot_by_protocol_id
    client.list_tasks.side_effect = list_tasks
    client.cancel_task.return_value = tasks["task-owned"].model_copy(update={"status": "canceled"})
    client.request_runtime_cancel.return_value = RuntimeState(
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="0.4.2",
        cancel_state="requested",
        working=True,
    )
    client.create_task.side_effect = create_task
    client.list_task_messages.return_value = []
    client.get_agent_card.return_value = AgentCardEnvelope(
        agent_session_uid=OWNED_SESSION,
        agent_uid="agent-1",
        agent_card={
            "name": "Reviewer",
            "capabilities": {
                "extensions": [
                    {
                        "uri": RESPONSE_KIND_EXTENSION_URI,
                        "params": {"supportedResponseKinds": ["message", "task"]},
                    }
                ]
            },
        },
    )
    return client


def _hosted_app(
    platform_keys,
    tmp_path,
    **overrides: Any,
) -> tuple[FastAPI, AsyncMock, _Manager]:
    config = platform_keys.hosted_settings(tmp_path, **overrides)
    app = create_app(config)
    client = _client(config)
    manager = _Manager(config, client)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[runtime_manager] = lambda: manager
    app.dependency_overrides[settings] = lambda: config
    return app, client, manager


def _user_headers(platform_keys, user_uid: str, *, admin: bool = False) -> dict[str, str]:
    return {
        "X-Caller-Kind": "user",
        ASSERTION_HEADER: platform_keys.caller_assertion(
            user_uid=user_uid,
            is_organization_admin=admin,
        ),
    }


def _message(session_uid: str, **extra: Any) -> dict[str, Any]:
    return {
        "message": {
            "messageId": "message-1",
            "role": "ROLE_USER",
            "contextId": session_uid,
            "parts": [{"text": "Review the session."}],
        },
        **extra,
    }


def _rpc(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": "rpc-1", "method": method, "params": params}


SESSION_ROUTES = {
    "chat": ("POST", "/api/chat", {"json": {"sessionUid": OWNED_SESSION, "message": "hello"}}, 200),
    "session_model": (
        "GET",
        "/api/chat/session-model",
        {"params": {"sessionUid": OWNED_SESSION}},
        200,
    ),
    "session_cancel": (
        "POST",
        "/api/chat/session/cancel",
        {"json": {"sessionUid": OWNED_SESSION}},
        200,
    ),
    "message_send": ("POST", f"{REST_BASE}/message:send", {"json": _message(OWNED_SESSION)}, 200),
    # The platform replays a finished Task, so an admitted stream sends one final event.
    "message_stream": (
        "POST",
        f"{REST_BASE}/message:stream",
        {"json": _message(OWNED_SESSION, taskId="task-new")},
        200,
    ),
    "task_get": ("GET", f"{REST_BASE}/tasks/task-owned", {}, 200),
    "task_cancel": ("POST", f"{REST_BASE}/tasks/task-owned:cancel", {}, 200),
    # The Task is terminal, so an admitted subscription answers the A2A unsupported error.
    "task_subscribe": ("GET", f"{REST_BASE}/tasks/task-owned:subscribe", {}, 400),
    "task_list": ("GET", f"{REST_BASE}/tasks", {"params": {"contextId": OWNED_SESSION}}, 200),
    "agent_card": (
        "GET",
        f"{REST_BASE}/extendedAgentCard",
        {"params": {"agent_session_uid": OWNED_SESSION}},
        200,
    ),
    "rpc_message_send": (
        "POST",
        "/api/a2a/rpc",
        {"json": _rpc("message/send", _message(OWNED_SESSION))},
        200,
    ),
    "rpc_task_get": (
        "POST",
        "/api/a2a/rpc",
        {"json": _rpc("tasks/get", {"id": "task-owned"})},
        200,
    ),
    "rpc_task_cancel": (
        "POST",
        "/api/a2a/rpc",
        {"json": _rpc("tasks/cancel", {"id": "task-owned"})},
        200,
    ),
    "rpc_task_list": (
        "POST",
        "/api/a2a/rpc",
        {"json": _rpc("tasks/list", {"contextId": OWNED_SESSION})},
        200,
    ),
    "rpc_task_subscribe": (
        "POST",
        "/api/a2a/rpc",
        {"json": _rpc("tasks/subscribe", {"id": "task-owned"})},
        200,
    ),
    "rpc_message_stream": (
        "POST",
        "/api/a2a/rpc",
        {"json": _rpc("message/stream", _message(OWNED_SESSION, taskId="task-new"))},
        200,
    ),
}


@pytest.mark.parametrize("route", sorted(SESSION_ROUTES))
@pytest.mark.parametrize(
    ("caller", "admitted"),
    [("owner", True), ("organization_admin", True), ("other_user", False)],
)
async def test_only_the_owner_or_an_organization_admin_addresses_a_session(
    served_platform_keys,
    tmp_path,
    asgi_client,
    route,
    caller,
    admitted,
):
    method, path, options, admitted_status = SESSION_ROUTES[route]
    app, client, manager = _hosted_app(served_platform_keys, tmp_path)
    headers = {
        "owner": _user_headers(served_platform_keys, OWNER_UID),
        "organization_admin": _user_headers(served_platform_keys, ADMIN_UID, admin=True),
        "other_user": _user_headers(served_platform_keys, OTHER_UID),
    }[caller]

    async with asgi_client(app) as http:
        response = await http.request(method, path, headers=headers, **options)

    if admitted:
        assert response.status_code == admitted_status, response.text
        if response.headers["content-type"].startswith("text/event-stream"):
            assert response.text.startswith("data: "), response.text
        elif path == "/api/a2a/rpc":
            body = response.json()
            # A terminal Task answers SubscribeToTask with the A2A unsupported-operation error.
            assert "result" in body or body["error"]["code"] == -32004, response.text
        return
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": SESSION_ACCESS_DENIED_DETAIL}
    assert manager.prompted == []
    assert manager.cancelled == []
    client.cancel_task.assert_not_awaited()
    client.request_runtime_cancel.assert_not_awaited()
    client.get_agent_card.assert_not_awaited()
    client.list_tasks.assert_not_awaited()
    client.create_task.assert_not_awaited()


async def test_an_organization_admin_is_admitted_without_reading_the_session(
    served_platform_keys,
    tmp_path,
    asgi_client,
):
    app, client, _manager = _hosted_app(served_platform_keys, tmp_path)

    async with asgi_client(app) as http:
        response = await http.post(
            "/api/chat/session/cancel",
            json={"sessionUid": FOREIGN_SESSION},
            headers=_user_headers(served_platform_keys, ADMIN_UID, admin=True),
        )

    assert response.status_code == 200
    client.get_session.assert_not_awaited()


@pytest.mark.parametrize(
    ("caller", "visible"),
    [
        ("owner", ["task-owned"]),
        ("other_user", ["task-foreign"]),
        ("organization_admin", ["task-owned", "task-foreign"]),
    ],
)
@pytest.mark.parametrize("transport", ["rest", "rpc"])
async def test_a_task_list_across_sessions_shows_only_sessions_the_caller_may_address(
    served_platform_keys,
    tmp_path,
    asgi_client,
    caller,
    visible,
    transport,
):
    app, _backend, _manager = _hosted_app(served_platform_keys, tmp_path)
    headers = {
        "owner": _user_headers(served_platform_keys, OWNER_UID),
        "organization_admin": _user_headers(served_platform_keys, ADMIN_UID, admin=True),
        "other_user": _user_headers(served_platform_keys, OTHER_UID),
    }[caller]

    async with asgi_client(app) as http:
        if transport == "rest":
            response = await http.get(f"{REST_BASE}/tasks", headers=headers)
            tasks = response.json()["tasks"]
        else:
            response = await http.post(
                "/api/a2a/rpc",
                json=_rpc("tasks/list", {}),
                headers=headers,
            )
            tasks = response.json()["result"]["tasks"]

    assert response.status_code == 200
    assert [task["id"] for task in tasks] == visible


@pytest.mark.parametrize("transport", ["rest", "rpc"])
async def test_an_existing_task_of_another_session_is_not_streamed(
    served_platform_keys,
    tmp_path,
    asgi_client,
    transport,
):
    # The caller owns the session it names, but the Task ID it sends belongs to a Task of
    # another user's session, which the platform returns as the existing Task.
    app, _backend, _manager = _hosted_app(served_platform_keys, tmp_path)
    body = _message(OWNED_SESSION, taskId="task-foreign")

    async with asgi_client(app) as http:
        if transport == "rest":
            response = await http.post(
                f"{REST_BASE}/message:stream",
                json=body,
                headers=_user_headers(served_platform_keys, OWNER_UID),
            )
        else:
            response = await http.post(
                "/api/a2a/rpc",
                json=_rpc("message/stream", body),
                headers=_user_headers(served_platform_keys, OWNER_UID),
            )

    assert response.status_code == 403
    assert response.json() == {"detail": SESSION_ACCESS_DENIED_DETAIL}


@pytest.mark.parametrize("body", ["continuation", "replayed_task_id"])
async def test_a_message_cannot_reach_a_task_of_another_session(
    served_platform_keys,
    tmp_path,
    asgi_client,
    body,
):
    # The caller owns the session it names, but the Task belongs to another user's session.
    app, client, manager = _hosted_app(served_platform_keys, tmp_path)
    message = _message(OWNED_SESSION, configuration={"responseKind": "task"})
    if body == "continuation":
        message["message"]["taskId"] = "task-foreign"
    else:
        message["taskId"] = "task-foreign"

    async with asgi_client(app) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            json=message,
            headers={
                **_user_headers(served_platform_keys, OWNER_UID),
                "A2A-Extensions": RESPONSE_KIND_EXTENSION_URI,
            },
        )

    assert response.status_code == 403
    assert response.json() == {"detail": SESSION_ACCESS_DENIED_DETAIL}
    client.continue_task.assert_not_awaited()
    assert manager.prompted == []


async def test_a_message_into_a_foreign_session_is_refused_before_its_parts_are_stored(
    served_platform_keys,
    tmp_path,
    asgi_client,
):
    assets = tmp_path / "assets"
    app, _backend, manager = _hosted_app(served_platform_keys, tmp_path, a2a_asset_root=assets)
    body = _message(FOREIGN_SESSION)
    body["message"]["parts"] = [
        {"raw": "JVBERi0xLjQK", "mediaType": "application/pdf", "filename": "report.pdf"}
    ]

    async with asgi_client(app) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            json=body,
            headers=_user_headers(served_platform_keys, OWNER_UID),
        )

    assert response.status_code == 403
    assert manager.prompted == []
    assert not any(assets.glob("**/*report.pdf"))


async def test_the_platform_addresses_any_session_with_a_platform_assertion(
    served_platform_keys,
    tmp_path,
    asgi_client,
):
    app, _backend, _manager = _hosted_app(served_platform_keys, tmp_path)

    async with asgi_client(app) as http:
        response = await http.post(
            "/internal/a2a/task-caller-delivery",
            json={
                "delivery_uid": "delivery-1",
                "task_uid": "backend-task-foreign",
                "task_id": "task-foreign",
                "status": "completed",
                "caller_agent_session_uid": FOREIGN_SESSION,
                "event_cursor": 1,
            },
            headers={ASSERTION_HEADER: served_platform_keys.platform_assertion()},
        )

    assert response.status_code == 200
    assert response.json()["accepted"] is True


async def test_a_user_turn_is_stamped_with_the_verified_caller_never_the_header(
    served_platform_keys,
    tmp_path,
    asgi_client,
):
    app, _backend, manager = _hosted_app(served_platform_keys, tmp_path)
    headers = _user_headers(served_platform_keys, OWNER_UID)
    headers["X-User-UID"] = OTHER_UID

    async with asgi_client(app) as http:
        response = await http.post(
            "/api/chat",
            json={"sessionUid": OWNED_SESSION, "message": "hello"},
            headers=headers,
        )

    assert response.status_code == 200
    assert manager.provenances[0]["actorKind"] == "user"
    assert manager.provenances[0]["actorUid"] == OWNER_UID


async def test_an_agent_turn_keeps_its_provenance_from_the_gateway_headers(
    served_platform_keys,
    tmp_path,
    asgi_client,
):
    # The calling Agent's runtime principal created the session, so it owns it.
    app, _backend, manager = _hosted_app(served_platform_keys, tmp_path)
    headers = {
        **AGENT_CALLER_HEADERS,
        ASSERTION_HEADER: served_platform_keys.caller_assertion(user_uid=OWNER_UID),
    }

    async with asgi_client(app) as http:
        response = await http.post(
            f"{REST_BASE}/message:send",
            json=_message(OWNED_SESSION),
            headers=headers,
        )

    assert response.status_code == 200
    assert manager.provenances[0]["actorKind"] == "agent"
    assert manager.provenances[0]["actorUid"] == AGENT_CALLER_HEADERS["X-Caller-Agent-UID"]


async def test_the_caller_kind_header_is_still_required(
    served_platform_keys,
    tmp_path,
    asgi_client,
):
    app, _backend, manager = _hosted_app(served_platform_keys, tmp_path)

    async with asgi_client(app) as http:
        response = await http.post(
            "/api/chat",
            json={"sessionUid": OWNED_SESSION, "message": "hello"},
            headers={ASSERTION_HEADER: served_platform_keys.caller_assertion()},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "runtime_caller_identity_invalid"
    assert manager.prompted == []


async def test_handlers_receive_the_verified_caller(served_platform_keys, tmp_path, asgi_client):
    app, _backend, _manager = _hosted_app(served_platform_keys, tmp_path)
    team_uid = "8c7d6e5f-4a3b-4c2d-9e1f-0a9b8c7d6e5f"

    @app.get("/caller")
    async def caller(request: Request) -> dict[str, object]:
        verified = current_caller()
        return {
            "current": asdict(verified) if verified else None,
            "user_uid": request.state.user_uid,
            "auth_outcome": request.state.auth_outcome,
            "resource_release_uid": request.state.resource_release_uid,
            "organization_environment_uid": request.state.organization_environment_uid,
        }

    async with asgi_client(app) as http:
        response = await http.get(
            "/caller",
            headers={
                ASSERTION_HEADER: served_platform_keys.caller_assertion(
                    team_uids=[team_uid],
                    is_organization_admin=True,
                )
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "current": {
            "uid": OWNER_UID,
            "team_uids": [team_uid],
            "is_organization_admin": True,
        },
        "user_uid": OWNER_UID,
        "auth_outcome": "authenticated",
        "resource_release_uid": served_platform_keys.release_uid,
        "organization_environment_uid": served_platform_keys.environment_uid,
    }
    assert current_caller() is None


@pytest.mark.parametrize(
    "path", ["/internal/a2a/task-dispatch", "/internal/a2a/task-caller-delivery"]
)
async def test_internal_routes_accept_only_a_platform_assertion(
    served_platform_keys,
    tmp_path,
    asgi_client,
    path,
):
    app, _backend, _manager = _hosted_app(served_platform_keys, tmp_path)

    async with asgi_client(app) as http:
        platform = await http.post(
            path,
            json={},
            headers={ASSERTION_HEADER: served_platform_keys.platform_assertion()},
        )
        caller = await http.post(
            path,
            json={},
            headers={
                "X-Caller-Kind": "user",
                ASSERTION_HEADER: served_platform_keys.caller_assertion(is_organization_admin=True),
            },
        )
        missing = await http.post(path, json={})

    # The handler ran for the platform and found the empty signal incomplete.
    assert platform.status_code == 400
    assert caller.status_code == 401
    assert caller.json() == {"detail": "A valid platform assertion is required."}
    assert missing.status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/api/chat/mock"), ("GET", "/version"), ("GET", "/no-such-route")],
)
async def test_every_other_route_accepts_only_a_caller_assertion(
    served_platform_keys,
    tmp_path,
    asgi_client,
    method,
    path,
):
    app, _backend, _manager = _hosted_app(served_platform_keys, tmp_path)
    options: dict[str, Any] = {"json": {"message": "hello"}} if method == "POST" else {}

    async with asgi_client(app) as http:
        caller = await http.request(
            method,
            path,
            headers={ASSERTION_HEADER: served_platform_keys.caller_assertion()},
            **options,
        )
        platform = await http.request(
            method,
            path,
            headers={ASSERTION_HEADER: served_platform_keys.platform_assertion()},
            **options,
        )
        missing = await http.request(method, path, **options)
        gateway_headers_only = await http.request(
            method,
            path,
            headers={"X-Caller-Kind": "user", "X-User-UID": OWNER_UID},
            **options,
        )

    assert caller.status_code == (404 if path == "/no-such-route" else 200)
    for rejected in (platform, missing, gateway_headers_only):
        assert rejected.status_code == 401
        assert rejected.json() == {"detail": "A valid caller assertion is required."}
        assert rejected.headers["cache-control"] == "no-store"


async def test_a_duplicated_invalid_or_expired_assertion_is_rejected(
    served_platform_keys,
    tmp_path,
    asgi_client,
):
    app, _backend, _manager = _hosted_app(served_platform_keys, tmp_path)
    valid = served_platform_keys.caller_assertion()
    expired = served_platform_keys.caller_assertion(now=1_700_000_000)

    async with asgi_client(app) as http:
        duplicated = await http.post(
            "/api/chat/mock",
            json={},
            headers=[(ASSERTION_HEADER, valid), (ASSERTION_HEADER, valid)],
        )
        invalid = await http.post("/api/chat/mock", json={}, headers={ASSERTION_HEADER: "x.y.z"})
        old = await http.post("/api/chat/mock", json={}, headers={ASSERTION_HEADER: expired})

    assert [duplicated.status_code, invalid.status_code, old.status_code] == [401, 401, 401]


async def test_an_unavailable_key_set_answers_503(served_platform_keys, tmp_path, asgi_client):
    served_platform_keys.status_code = 503
    app, _backend, _manager = _hosted_app(served_platform_keys, tmp_path)

    async with asgi_client(app) as http:
        response = await http.post(
            "/api/chat/mock",
            json={},
            headers={ASSERTION_HEADER: served_platform_keys.caller_assertion()},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Caller authentication is unavailable."}
    assert response.headers["cache-control"] == "no-store"


async def test_options_requests_pass_without_an_assertion(
    served_platform_keys,
    tmp_path,
    asgi_client,
):
    with_cors = create_app(
        served_platform_keys.hosted_settings(tmp_path, trusted_origins=("https://app.test",))
    )
    without_cors = create_app(served_platform_keys.hosted_settings(tmp_path))
    preflight_headers = {
        "Origin": "https://app.test",
        "Access-Control-Request-Method": "POST",
    }

    async with asgi_client(with_cors) as http:
        preflight = await http.options("/api/chat", headers=preflight_headers)
    async with asgi_client(without_cors) as http:
        options = await http.options("/api/chat")

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://app.test"
    # Without CORS the request reaches the router, which has no OPTIONS operation.
    assert options.status_code == 405
    assert served_platform_keys.fetches == 0


async def test_a_websocket_without_an_assertion_is_closed(served_platform_keys, tmp_path):
    app, _backend, _manager = _hosted_app(served_platform_keys, tmp_path)
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": "/api/chat",
            "raw_path": b"/api/chat",
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
            "subprotocols": [],
        },
        receive,
        send,
    )

    assert sent[0]["type"] == "websocket.close"
    assert sent[0]["code"] == 1008


async def test_a_rejected_request_is_logged_without_identity_or_token(
    served_platform_keys,
    tmp_path,
    asgi_client,
    capsys,
):
    app, _backend, _manager = _hosted_app(served_platform_keys, tmp_path)
    wrong_kind = served_platform_keys.platform_assertion()

    async with asgi_client(app) as http:
        response = await http.get(
            "/version",
            headers={"X-User-UID": OTHER_UID, ASSERTION_HEADER: wrong_kind},
        )

    output = capsys.readouterr().out
    events = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    completed = next(event for event in events if event["event"] == "http.request.completed")
    rejected = next(event for event in events if event["event"] == "request_identity.rejected")
    assert response.status_code == 401
    assert completed["auth_outcome"] == "rejected"
    assert completed["principal_type"] == "anonymous"
    assert "user_uid" not in completed
    assert rejected["required_assertion"] == "caller"
    assert wrong_kind not in output


def test_a_hosted_app_declares_assertion_request_identity(platform_keys, tmp_path):
    app = create_app(platform_keys.hosted_settings(tmp_path))

    declaration = app.state.mainsequence_request_identity
    assert declaration == {"installed": True, "mode": "assertion", "public_ingress": ()}
    assert type(declaration["public_ingress"]) is tuple
    assert any(entry.cls is RequestIdentityMiddleware for entry in app.user_middleware)


def test_a_managed_app_outside_hosting_declares_local_request_identity(test_settings):
    app = create_app(test_settings)

    assert app.state.mainsequence_request_identity == {
        "installed": True,
        "mode": "local",
        "public_ingress": (),
    }
    assert not any(entry.cls is RequestIdentityMiddleware for entry in app.user_middleware)


def test_a_local_mode_app_declares_local_request_identity(tmp_path):
    app = create_app(
        TauSDKSettings(
            _env_file=None,
            workspace=tmp_path,
            local_state_root=tmp_path / "state",
            auth_mode="jwt",
            local_mode=True,
            access_token="access-token",
            refresh_token="refresh-token",
            local_provider="openai",
            local_model="gpt-5.4",
        )
    )

    assert app.state.mainsequence_request_identity == {
        "installed": True,
        "mode": "local",
        "public_ingress": (),
    }
    assert not any(entry.cls is RequestIdentityMiddleware for entry in app.user_middleware)


async def test_outside_hosting_requests_are_handled_as_before(test_settings, asgi_client):
    app = create_app(test_settings)

    async with asgi_client(app) as http:
        response = await http.post("/api/chat/mock", json={"message": "hello"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("environment", "mode"),
    [
        ({}, "local"),
        ({"MAINSEQUENCE_CALLER_AUTH_MODE": "local"}, "local"),
        ({"MAINSEQUENCE_CALLER_AUTH_MODE": ""}, "local"),
        ({"APP_NAME": ""}, "local"),
        (
            {"MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"},
            "local",
        ),
        ({"MAINSEQUENCE_CALLER_AUTH_MODE": "assertion"}, "assertion"),
        ({"APP_NAME": "6f1c2b8e-4d3a-4e5f-9a8b-7c6d5e4f3a2b"}, "assertion"),
        ({"FASTAPI_PUBLIC_BASE_URL": "https://agent.test"}, "assertion"),
        ({"MAINSEQUENCE_CALLER_ASSERTION_ISSUER": "https://platform.test"}, "assertion"),
        ({"MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL": "https://platform.test/keys/"}, "assertion"),
        (
            {"MAINSEQUENCE_CALLER_AUTH_MODE": "local", "APP_NAME": "my-app"},
            "assertion",
        ),
    ],
)
def test_hosted_mode_follows_the_launcher_rule(monkeypatch, tmp_path, environment, mode):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    config = TauSDKSettings(
        _env_file=None,
        runtime_credential_id="credential-id",
        workspace=tmp_path,
    )

    assert config.request_identity_mode == mode


def test_the_platform_environment_configures_a_hosted_app(monkeypatch, platform_keys, tmp_path):
    for name, value in {
        "MAINSEQUENCE_CALLER_AUTH_MODE": "assertion",
        "MAINSEQUENCE_CALLER_ASSERTION_ISSUER": platform_keys.issuer,
        "MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL": platform_keys.jwks_url,
        "APP_NAME": platform_keys.release_uid,
        "MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID": platform_keys.environment_uid,
    }.items():
        monkeypatch.setenv(name, value)

    app = create_app(
        TauSDKSettings(
            _env_file=None,
            runtime_credential_id="credential-id",
            workspace=tmp_path,
        )
    )

    assert app.state.mainsequence_request_identity["mode"] == "assertion"


def test_an_unknown_caller_auth_mode_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("MAINSEQUENCE_CALLER_AUTH_MODE", "Assertion")

    with pytest.raises(ValidationError, match="MAINSEQUENCE_CALLER_AUTH_MODE"):
        TauSDKSettings(
            _env_file=None,
            runtime_credential_id="credential-id",
            workspace=tmp_path,
        )


def test_local_mode_refuses_hosted_caller_authentication(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_NAME", "6f1c2b8e-4d3a-4e5f-9a8b-7c6d5e4f3a2b")

    with pytest.raises(ValidationError, match="TAU_LOCAL_MODE cannot run with hosted"):
        TauSDKSettings(
            _env_file=None,
            workspace=tmp_path,
            auth_mode="jwt",
            local_mode=True,
            local_provider="openai",
            local_model="gpt-5.4",
        )


def test_an_incomplete_hosted_configuration_fails_app_construction(platform_keys, tmp_path):
    config = platform_keys.hosted_settings(tmp_path, organization_environment_uid=None)

    with pytest.raises(ConfigurationError, match="MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID"):
        create_app(config)
