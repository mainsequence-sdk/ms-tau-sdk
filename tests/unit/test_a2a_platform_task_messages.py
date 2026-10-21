"""Hosted Task Messages: the Messages the platform stores reach A2A callers as A2A Messages.

The fake platform answers in the shapes of its own serializers: a Task Message has the role
``user`` or ``agent`` and snake-case fields, while a Task's ``status_message`` is already a binding
Message. The SDK runs its real platform client against it.
"""

import contextlib
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from structlog.testing import capture_logs

from ms_tau_sdk.api.a2a import (
    RESPONSE_KIND_EXTENSION_URI,
    REST_BASE,
    _durable_task_execution_input,
    router,
)
from ms_tau_sdk.api.dependencies import backend, runtime_manager, settings
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.protocols.a2a_roles import platform_message_to_binding
from ms_tau_sdk.settings import TauSDKSettings

SESSION_UID = "session-1"
TASK_UID = "backend-task-1"
TASK_ID = "task-1"
STATUS_DETAIL = "https://mainsequence.ai/a2a/extensions/task-status-detail/v1"
TRACE = "https://example.test/a2a/extensions/trace/v1"
SEND_HEADERS = {
    "A2A-Extensions": RESPONSE_KIND_EXTENSION_URI,
    "X-Caller-Kind": "user",
    "X-User-UID": "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d",
    "X-Username": "jose",
}


def _platform_message(sequence: int, role: str, message_id: str, text: str) -> dict[str, Any]:
    """A Task Message as the platform's AgentTaskMessageSerializer answers it."""

    return {
        "uid": f"message-row-{sequence}",
        "task_uid": TASK_UID,
        "agent_uid": "agent-1",
        "agent_session_uid": SESSION_UID,
        "message_id": message_id,
        "request_hash": f"sha256:{sequence:064d}",
        "role": role,
        "parts": [{"text": text}],
        "metadata": {},
        "extensions": [],
        "reference_task_ids": [],
        "sequence": sequence,
        "created_at": "2026-10-21T08:30:03Z",
        "created_by_user_uid": "user-1",
        "requester_user_uid": "user-1",
        "requester_identity_type": "human",
    }


REQUESTER = _platform_message(0, "user", "message-1", "Read CRM settings.")
RESPONDER = {
    **_platform_message(1, "agent", "message-agent-1", "Which account should be used?"),
    "extensions": [STATUS_DETAIL],
    "metadata": {STATUS_DETAIL: {"code": "input_required", "retryable": False}},
}
# The platform keeps a Task's status Message as the binding Message it accepted.
STATUS_MESSAGE = {
    "messageId": "message-agent-1",
    "role": "ROLE_RESPONDER",
    "parts": [{"text": "Which account should be used?"}],
    "taskId": TASK_ID,
    "contextId": SESSION_UID,
    "extensions": [STATUS_DETAIL],
    "metadata": {STATUS_DETAIL: {"code": "input_required", "retryable": False}},
}


# A finalized answer as the platform's AgentTaskOutputSerializer answers it. An error answer
# is an answer too, and reaches the caller like any other.
ANSWER = {
    "uid": "output-row-1",
    "task_uid": TASK_UID,
    "artifact_id": "answer-1",
    "name": "answer",
    "description": "",
    "parts": [{"text": '{"tool":"crm_settings_get","code":"forbidden","settings":null}'}],
    "metadata": {},
    "extensions": [],
    "revision": 17,
    "finalized": True,
    "finalized_at": "2026-10-21T10:31:57Z",
    "created_at": "2026-10-21T10:30:44Z",
    "updated_at": "2026-10-21T10:31:57Z",
    "created_by_user_uid": "workload-user-1",
}
ANSWER_ARTIFACT = {"artifactId": "answer-1", "name": "answer", "parts": ANSWER["parts"]}


def _a2a_message(record: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "messageId": record["message_id"],
        "contextId": SESSION_UID,
        "taskId": TASK_ID,
        "role": role,
        "parts": record["parts"],
        "metadata": record["metadata"],
        "extensions": record["extensions"],
        "referenceTaskIds": record["reference_task_ids"],
    }


class _Platform:
    """The platform's Task API for one Task, answering in its own serializers' shapes."""

    def __init__(
        self,
        *,
        messages: list[dict[str, Any]],
        status: str = "submitted",
        outputs: list[dict[str, Any]] | None = None,
    ) -> None:
        self.messages = messages
        self.status = status
        self.outputs = outputs or []
        self.broken_history = False
        self.requests: list[tuple[str, str, str]] = []

    def _row(self, *, status: str, history_length: int = 0) -> dict[str, Any]:
        """A Task as the platform's Task list answers it: counts, never output contents."""

        row: dict[str, Any] = {
            "uid": TASK_UID,
            "task_id": TASK_ID,
            "context_id": SESSION_UID,
            "agent_uid": "agent-1",
            "agent_session_uid": SESSION_UID,
            "status": status,
            "status_message": STATUS_MESSAGE if status == "input_required" else None,
            "latest_message_preview": self.messages[-1]["parts"][0]["text"],
            "output_count": len(self.outputs),
            "finalized_output_count": sum(output["finalized"] for output in self.outputs),
        }
        if history_length:
            row["history"] = self._tail(history_length)
        return row

    def _task(self, *, status: str, history_length: int = 0) -> dict[str, Any]:
        """A Task as the platform's Task detail, snapshot and runtime answers give it."""

        task: dict[str, Any] = {
            **self._row(status=status, history_length=history_length),
            "latest_message": self.messages[-1] if status != "submitted" else self.messages[0],
            "outputs": self.outputs,
        }
        del task["latest_message_preview"], task["output_count"], task["finalized_output_count"]
        return task

    def _tail(self, history_length: int) -> list[dict[str, Any]]:
        if self.broken_history:
            return [{**REQUESTER, "role": "system"}]
        return self.messages[-history_length:] if history_length else []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        self.requests.append((request.method, path, request.url.query.decode()))
        history_length = int(params.get("history_length", 0))
        if path == f"/api/v1/agent-sessions/{SESSION_UID}/":
            return httpx.Response(
                200,
                json={
                    "uid": SESSION_UID,
                    "agent_uid": "agent-1",
                    "harness": "tau",
                    "harness_protocol": "tau-session-v1",
                    "harness_version": "0.4.2",
                },
            )
        if path == f"/api/v1/agent-sessions/{SESSION_UID}/agent-card/":
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": SESSION_UID,
                    "agent_uid": "agent-1",
                    "agent_card": {
                        "capabilities": {
                            "extensions": [
                                {
                                    "uri": RESPONSE_KIND_EXTENSION_URI,
                                    "params": {"supportedResponseKinds": ["message", "task"]},
                                }
                            ]
                        }
                    },
                },
            )
        if path == "/api/v1/agent-tasks/" and request.method == "POST":
            return httpx.Response(201, json=self._task(status="submitted"))
        if path == "/api/v1/agent-tasks/" and request.method == "GET":
            row = self._row(status=self.status, history_length=history_length)
            return httpx.Response(200, json={"results": [row]})
        if path == f"/api/v1/agent-tasks/{TASK_UID}/":
            task = self._task(status=self.status, history_length=history_length)
            return httpx.Response(200, json=task)
        if path == f"/api/v1/agent-tasks/{TASK_UID}/snapshot/":
            return httpx.Response(
                200,
                json={"task": self._task(status=self.status), "event_cursor": 1},
            )
        if path == f"/api/v1/agent-tasks/{TASK_UID}/messages/":
            if "history_length" not in params:
                # Without a tail length the platform pages the conversation oldest first.
                offset = int(params.get("offset", 0))
                page = self.messages[offset : offset + int(params.get("limit", 100))]
                return httpx.Response(200, json={"results": page})
            if "limit" in params or "offset" in params:
                return httpx.Response(400, json={"history_length": "Not with limit or offset."})
            tail = self._tail(history_length)
            return httpx.Response(
                200,
                json={"count": len(tail), "next": None, "previous": None, "results": tail},
            )
        if path == f"/api/v1/agent-tasks/{TASK_UID}/dispatches/":
            # No open dispatch: the request does not start the Task itself.
            return httpx.Response(200, json=[])
        return httpx.Response(404)


class _RuntimeAuth:
    def bind_client(self, client: httpx.AsyncClient) -> None:
        del client

    async def headers(self, *, force: bool = False) -> dict[str, str]:
        del force
        return {"Authorization": "Bearer runtime-token"}

    async def prefetch(self) -> None:
        return None


def _settings() -> TauSDKSettings:
    return TauSDKSettings(
        _env_file=None,
        backend_url="https://platform.test",
        runtime_credential_id="credential-id",
        a2a_task_event_poll_interval_seconds=0.05,
        a2a_task_wait_timeout_seconds=5,
    )


@contextlib.asynccontextmanager
async def _platform_client(platform: _Platform) -> AsyncIterator[MainSequenceClient]:
    config = _settings()
    async with httpx.AsyncClient(
        base_url=config.backend_url,
        transport=httpx.MockTransport(platform.handle),
    ) as platform_http:
        yield MainSequenceClient(config, _RuntimeAuth(), client=platform_http)  # type: ignore[arg-type]


@contextlib.asynccontextmanager
async def _hosted_tau(platform: _Platform) -> AsyncIterator[httpx.AsyncClient]:
    async with _platform_client(platform) as client:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[backend] = lambda: client
        app.dependency_overrides[runtime_manager] = lambda: SimpleNamespace(holder_id="holder-1")
        app.dependency_overrides[settings] = lambda: client.settings
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://tau.test",
        ) as http:
            yield http


def _send_body(**configuration: Any) -> dict[str, Any]:
    return {
        "message": {
            "messageId": "message-1",
            "role": "ROLE_USER",
            "contextId": SESSION_UID,
            "parts": [{"text": "Read CRM settings."}],
        },
        "configuration": {"responseKind": "task", **configuration},
    }


async def _send(http: httpx.AsyncClient, **configuration: Any) -> httpx.Response:
    return await http.post(
        f"{REST_BASE}/message:send",
        headers=SEND_HEADERS,
        json=_send_body(**configuration),
    )


def test_a_platform_message_becomes_a_binding_message_of_its_task():
    record = {**RESPONDER, "extensions": [TRACE, STATUS_DETAIL], "reference_task_ids": ["task-0"]}

    message = platform_message_to_binding(record, context_id=SESSION_UID, task_id=TASK_ID)

    # Identity, Parts, metadata, extension order and references are kept; fields only the
    # platform keeps, such as the request hash and the requester, are left out.
    assert message == {
        "messageId": "message-agent-1",
        "contextId": SESSION_UID,
        "taskId": TASK_ID,
        "role": "ROLE_RESPONDER",
        "parts": [{"text": "Which account should be used?"}],
        "metadata": {STATUS_DETAIL: {"code": "input_required", "retryable": False}},
        "extensions": [TRACE, STATUS_DETAIL],
        "referenceTaskIds": ["task-0"],
    }
    assert platform_message_to_binding(REQUESTER, context_id=SESSION_UID, task_id=TASK_ID)[
        "role"
    ] == ("ROLE_REQUESTER")


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"role": "ROLE_REQUESTER"}, "role must be user or agent"),
        ({"role": "ROLE_USER"}, "role must be user or agent"),
        ({"role": "system"}, "role must be user or agent"),
        ({"message_id": ""}, "messageId is required"),
        ({"parts": []}, "parts must be a non-empty array"),
    ],
)
def test_a_platform_message_outside_the_contract_is_rejected(change, error):
    with pytest.raises(ValueError, match=error):
        platform_message_to_binding(
            {**REQUESTER, **change},
            context_id=SESSION_UID,
            task_id=TASK_ID,
        )


@pytest.mark.asyncio
async def test_a_hosted_task_send_answers_with_the_platforms_messages():
    platform = _Platform(messages=[REQUESTER])

    async with _hosted_tau(platform) as http:
        response = await _send(http, returnImmediately=True)

    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["id"] == TASK_ID
    assert task["status"]["state"] == "TASK_STATE_SUBMITTED"
    assert task["history"] == [_a2a_message(REQUESTER, "ROLE_USER")]
    assert ("GET", f"/api/v1/agent-tasks/{TASK_UID}/messages/", "history_length=100") in (
        platform.requests
    )


@pytest.mark.asyncio
async def test_a_hosted_task_send_that_waits_answers_with_both_directions():
    platform = _Platform(messages=[REQUESTER, RESPONDER], status="input_required")

    async with _hosted_tau(platform) as http:
        response = await _send(http, returnImmediately=False)

    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert task["status"]["message"]["role"] == "ROLE_AGENT"
    assert task["history"] == [
        _a2a_message(REQUESTER, "ROLE_USER"),
        _a2a_message(RESPONDER, "ROLE_AGENT"),
    ]


@pytest.mark.asyncio
async def test_a_hosted_task_send_answers_with_the_latest_messages():
    platform = _Platform(messages=[REQUESTER, RESPONDER], status="input_required")

    async with _hosted_tau(platform) as http:
        response = await _send(http, returnImmediately=False, historyLength=1)

    assert response.status_code == 200, response.text
    assert response.json()["task"]["history"] == [_a2a_message(RESPONDER, "ROLE_AGENT")]
    assert ("GET", f"/api/v1/agent-tasks/{TASK_UID}/messages/", "history_length=1") in (
        platform.requests
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "platform_query", "message_ids"),
    [
        ("", "history_length=100", ["message-1", "message-agent-1"]),
        ("?historyLength=1", "history_length=1", ["message-agent-1"]),
        ("?historyLength=0", "history_length=0", None),
    ],
)
async def test_a_hosted_task_get_answers_with_the_platforms_messages(
    query,
    platform_query,
    message_ids,
):
    platform = _Platform(messages=[REQUESTER, RESPONDER], status="input_required")

    async with _hosted_tau(platform) as http:
        response = await http.get(f"{REST_BASE}/tasks/{TASK_ID}{query}")

    assert response.status_code == 200, response.text
    task = response.json()["task"]
    # The Task list finds the Task; its detail answers it.
    assert platform.requests == [
        ("GET", "/api/v1/agent-tasks/", "task_id=task-1"),
        ("GET", f"/api/v1/agent-tasks/{TASK_UID}/", platform_query),
    ]
    if message_ids is None:
        assert "history" not in task
    else:
        assert [message["messageId"] for message in task["history"]] == message_ids
        assert {message["role"] for message in task["history"]} <= {"ROLE_USER", "ROLE_AGENT"}


@pytest.mark.asyncio
async def test_a_hosted_task_list_answers_with_the_platforms_messages():
    platform = _Platform(messages=[REQUESTER, RESPONDER], status="input_required")

    async with _hosted_tau(platform) as http:
        response = await http.get(
            f"{REST_BASE}/tasks",
            params={"contextId": SESSION_UID, "historyLength": 2},
        )

    assert response.status_code == 200, response.text
    assert response.json()["tasks"][0]["history"] == [
        _a2a_message(REQUESTER, "ROLE_USER"),
        _a2a_message(RESPONDER, "ROLE_AGENT"),
    ]


@pytest.mark.asyncio
async def test_a_hosted_task_get_answers_with_its_persisted_answer():
    platform = _Platform(messages=[REQUESTER], status="completed", outputs=[ANSWER])

    async with _hosted_tau(platform) as http:
        response = await http.get(f"{REST_BASE}/tasks/{TASK_ID}")

    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["id"] == TASK_ID
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"] == [ANSWER_ARTIFACT]


@pytest.mark.asyncio
async def test_a_hosted_json_rpc_get_task_answers_with_its_persisted_answer():
    platform = _Platform(messages=[REQUESTER], status="completed", outputs=[ANSWER])

    async with _hosted_tau(platform) as http:
        response = await http.post(
            "/api/a2a/rpc",
            json={"jsonrpc": "2.0", "id": 3, "method": "GetTask", "params": {"id": TASK_ID}},
        )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["task"]["artifacts"] == [ANSWER_ARTIFACT]


@pytest.mark.asyncio
async def test_a_hosted_task_lookup_reads_the_complete_task():
    platform = _Platform(messages=[REQUESTER], status="completed", outputs=[ANSWER])

    async with _platform_client(platform) as client:
        task = await client.get_task_by_protocol_id(TASK_ID)
        snapshot = await client.get_task_snapshot_by_protocol_id(TASK_ID)

    assert task.outputs == snapshot.task.outputs == [ANSWER]
    assert task.latest_message == platform_message_to_binding(
        REQUESTER, context_id=SESSION_UID, task_id=TASK_ID
    )
    # Each lookup finds the Task in the list once, then reads the Task itself.
    assert platform.requests == [
        ("GET", "/api/v1/agent-tasks/", "task_id=task-1"),
        ("GET", f"/api/v1/agent-tasks/{TASK_UID}/", ""),
        ("GET", "/api/v1/agent-tasks/", "task_id=task-1"),
        ("GET", f"/api/v1/agent-tasks/{TASK_UID}/snapshot/", ""),
    ]


@pytest.mark.asyncio
async def test_the_dispatch_wake_up_runs_the_platforms_requester_message():
    platform = _Platform(messages=[REQUESTER])

    async with _platform_client(platform) as client:
        task = await client.get_task(TASK_UID)

    prompt, _contract, _provenance = _durable_task_execution_input(task, _settings())

    assert "Read CRM settings." in prompt


@pytest.mark.asyncio
async def test_a_send_that_fails_after_its_task_was_accepted_names_the_task():
    platform = _Platform(messages=[REQUESTER])
    platform.broken_history = True

    with capture_logs() as logs:
        async with _hosted_tau(platform) as http:
            response = await _send(http, returnImmediately=True)

    assert ("POST", "/api/v1/agent-tasks/", "") in platform.requests
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/a2a+json")
    error = response.json()["error"]
    assert error["status"] == "INTERNAL"
    assert TASK_ID in error["message"]
    assert "GetTask" in error["message"]
    assert error["details"] == [
        {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": "ACCEPTED_TASK_STATE_UNAVAILABLE",
            "domain": "mainsequence.ai",
            "metadata": {"taskId": TASK_ID, "contextId": SESSION_UID},
        }
    ]
    failure = next(log for log in logs if log["event"] == "a2a.task.answer_failed")
    assert failure["task_id"] == TASK_ID
    assert failure["error_type"] == "BackendError"


@pytest.mark.asyncio
async def test_a_json_rpc_send_that_fails_after_its_task_was_accepted_names_the_task():
    platform = _Platform(messages=[REQUESTER])
    platform.broken_history = True

    async with _hosted_tau(platform) as http:
        response = await http.post(
            "/api/a2a/rpc",
            headers=SEND_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "SendMessage",
                "params": _send_body(returnImmediately=True),
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 7
    assert body["error"]["code"] == -32603
    assert TASK_ID in body["error"]["message"]
    assert body["error"]["data"][0]["metadata"] == {"taskId": TASK_ID, "contextId": SESSION_UID}
