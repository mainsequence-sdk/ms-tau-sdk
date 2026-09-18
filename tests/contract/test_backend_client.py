import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ms_tau_sdk.backend.auth import RuntimeCredentialAuth
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.models import (
    RuntimeLeaseReleaseRequest,
    RuntimeLeaseRenewRequest,
    RuntimeLeaseRequest,
    RuntimeStatePatch,
    SessionEntryAppendRequest,
    SessionEntryRecord,
)
from ms_tau_sdk.backend.routes import RuntimeLeaseOperation, agent_session_runtime_lease
from ms_tau_sdk.errors import BackendError
from ms_tau_sdk.settings import TauSDKSettings


@pytest.mark.asyncio
async def test_python_client_matches_existing_django_session_contract():
    session_uid = "session-1"
    requests: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = json.loads(request.content) if request.content else None
        requests.append((request.method, path, payload))
        if path == "/api/v1/runtime-credentials/token/":
            return httpx.Response(200, json={"access": "runtime-token"})
        if path == f"/api/v1/agent-sessions/{session_uid}/" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "uid": session_uid,
                    "agent_uid": "agent-1",
                    "harness": "tau",
                    "harness_protocol": "tau-session-v1",
                    "harness_version": "0.4.2",
                    "llm_provider": "openai",
                    "llm_model": "gpt-5.4",
                    "llm_thinking": "high",
                    "created_by_user_uid": "user-1",
                },
            )
        if path == f"/api/v1/agent-sessions/{session_uid}/" and request.method == "PATCH":
            assert payload == {
                "llm_provider": "anthropic",
                "llm_model": "claude-sonnet-4-20250514",
                "llm_thinking": "high",
            }
            return httpx.Response(
                200,
                json={
                    "uid": session_uid,
                    "agent_uid": "agent-1",
                    "harness": "tau",
                    "harness_protocol": "tau-session-v1",
                    "harness_version": "0.4.2",
                    **payload,
                    "created_by_user_uid": "user-1",
                },
            )
        if path == f"/api/v1/agent-sessions/{session_uid}/agent-card/":
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "agent_uid": "agent-1",
                    "agent_card": {"name": "Main Sequence TAU SDK"},
                },
            )
        if path == f"/api/v1/agent-sessions/{session_uid}/entries/" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {
                            "sequence": 0,
                            "entry_type": "label",
                            "entry_json": {
                                "id": "entry-1",
                                "parent_id": None,
                                "timestamp": 1784851200.0,
                                "type": "label",
                                "target_id": "entry-1",
                                "label": "Session label",
                            },
                            "idempotency_key": "entry-1",
                        }
                    ],
                    "next_sequence": 1,
                    "has_more": False,
                    "next_after_sequence": None,
                },
            )
        if path == f"/api/v1/agent-sessions/{session_uid}/entries/append/":
            assert payload == {
                "lease_token": "lease-token",
                "expected_sequence": 1,
                "idempotency_key": "entry-2",
                "entry": {
                    "id": "entry-2",
                    "parent_id": "entry-1",
                    "timestamp": 1784851201.0,
                    "type": "label",
                    "target_id": "entry-1",
                    "label": "Updated label",
                },
            }
            return httpx.Response(
                201,
                json={
                    "sequence": 1,
                    "entry_type": "label",
                    "entry_json": payload["entry"],
                    "idempotency_key": "entry-2",
                },
            )
        if path == f"/api/v1/agent-sessions/{session_uid}/runtime-lease/acquire/":
            assert payload == {
                "holder_id": "ms-tau-1",
                "ttl_seconds": 90,
            }
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "holder_id": "ms-tau-1",
                    "lease_token": "lease-token",
                    "lease_expires_at": "2026-07-24T00:00:00Z",
                    "agent_session_status": "running",
                    "working": True,
                    "cancel_requested": False,
                    "cancellation": None,
                },
            )
        if path == f"/api/v1/agent-sessions/{session_uid}/runtime-lease/renew/":
            assert payload == {
                "lease_token": "lease-token",
                "holder_id": "ms-tau-1",
                "ttl_seconds": 90,
            }
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "holder_id": payload["holder_id"],
                    "lease_token": payload["lease_token"],
                    "lease_expires_at": "2026-07-24T00:01:00Z",
                    "agent_session_status": "running",
                    "working": True,
                    "cancel_requested": False,
                    "cancellation": None,
                },
            )
        if path == f"/api/v1/agent-sessions/{session_uid}/runtime-lease/release/":
            assert payload == {
                "lease_token": "lease-token",
                "holder_id": "ms-tau-1",
                "reason": "runtime_eviction",
            }
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "released": True,
                },
            )
        if (
            path == f"/api/v1/agent-sessions/{session_uid}/runtime-state/"
            and request.method == "PATCH"
        ):
            assert payload == {
                "active_provider": "openai",
            }
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "harness": "tau",
                    "harness_protocol": "tau-session-v1",
                    "harness_version": "0.4.2",
                    **payload,
                    "status": "running",
                    "runtime_state": "working",
                    "working": True,
                },
            )
        if path == f"/api/v1/agent-sessions/{session_uid}/runtime-state/":
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "harness": "tau",
                    "harness_protocol": "tau-session-v1",
                    "harness_version": "0.4.2",
                    "status": "running",
                    "runtime_state": "working",
                    "working": True,
                },
            )
        if path == f"/api/v1/agent-sessions/{session_uid}/runtime-cancel-request/":
            assert payload == {
                "reason": "user_requested",
                "message": "stop",
                "requested_by_holder_id": "ms-tau-1",
            }
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "harness": "tau",
                    "harness_protocol": "tau-session-v1",
                    "harness_version": "0.4.2",
                    "status": "running",
                    "runtime_state": "working",
                    "working": True,
                    "cancel_state": "requested",
                    "cancel_requested": True,
                    "cancellation_id": "00000000-0000-4000-8000-000000000001",
                },
            )
        return httpx.Response(404)

    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
    )
    async with httpx.AsyncClient(
        base_url=settings.backend_url,
        transport=httpx.MockTransport(handler),
    ) as http:
        auth = RuntimeCredentialAuth(settings, exchange_client=http)
        client = MainSequenceClient(settings, auth, client=http)

        session = await client.get_session(session_uid)
        card = await client.get_agent_card(session_uid)
        entries = await client.get_entries(session_uid)
        updated = await client.update_session_config(
            session_uid,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            thinking_level="high",
        )
        lease = await client.acquire_runtime_lease(
            session_uid,
            RuntimeLeaseRequest(holder_id="ms-tau-1", ttl_seconds=90),
        )
        appended = await client.append_entry(
            session_uid,
            SessionEntryAppendRequest(
                lease_token=lease.lease_token,
                expected_sequence=1,
                idempotency_key="entry-2",
                entry={
                    "id": "entry-2",
                    "parent_id": "entry-1",
                    "timestamp": 1784851201.0,
                    "type": "label",
                    "target_id": "entry-1",
                    "label": "Updated label",
                },
            ),
        )
        cancelled = await client.request_runtime_cancel(
            session_uid,
            message="stop",
            requested_by_holder_id="ms-tau-1",
        )
        renewed = await client.renew_runtime_lease(
            session_uid,
            RuntimeLeaseRenewRequest(
                lease_token=lease.lease_token,
                holder_id="ms-tau-1",
                ttl_seconds=90,
            ),
        )
        state = await client.get_runtime_state(session_uid)
        patched_state = await client.patch_runtime_state(
            session_uid,
            RuntimeStatePatch(
                active_provider="openai",
            ),
        )
        await client.release_runtime_lease(
            session_uid,
            RuntimeLeaseReleaseRequest(
                lease_token=lease.lease_token,
                holder_id="ms-tau-1",
                reason="runtime_eviction",
            ),
        )

    assert session.harness == "tau"
    assert session.active_provider == "openai"
    assert session.active_model == "gpt-5.4"
    assert session.active_thinking == "high"
    assert card.agent_card == {"name": "Main Sequence TAU SDK"}
    assert entries.next_sequence == 1
    assert entries.entries[0].entry_json["label"] == "Session label"
    assert appended.sequence == 1
    assert updated.active_provider == "anthropic"
    assert updated.active_model == "claude-sonnet-4-20250514"
    assert cancelled.cancel_state == "requested"
    assert renewed.checkpoint_version == 0
    assert state.runtime_state == "working"
    assert patched_state.active_provider == "openai"
    assert [path for _, path, _ in requests] == [
        "/api/v1/runtime-credentials/token/",
        f"/api/v1/agent-sessions/{session_uid}/",
        f"/api/v1/agent-sessions/{session_uid}/agent-card/",
        f"/api/v1/agent-sessions/{session_uid}/entries/",
        f"/api/v1/agent-sessions/{session_uid}/",
        f"/api/v1/agent-sessions/{session_uid}/runtime-lease/acquire/",
        f"/api/v1/agent-sessions/{session_uid}/entries/append/",
        f"/api/v1/agent-sessions/{session_uid}/runtime-cancel-request/",
        f"/api/v1/agent-sessions/{session_uid}/runtime-lease/renew/",
        f"/api/v1/agent-sessions/{session_uid}/runtime-state/",
        f"/api/v1/agent-sessions/{session_uid}/runtime-state/",
        f"/api/v1/agent-sessions/{session_uid}/runtime-lease/release/",
    ]


def test_session_entry_record_accepts_tau_0_4_2_custom_message_discriminator():
    record = SessionEntryRecord(
        sequence=0,
        entry_type="custom_message",
        entry_json={
            "id": "custom-message",
            "type": "custom_message",
            "custom_type": "io.mainsequence.test",
            "content": "Extension context",
        },
        idempotency_key="custom-message",
    )

    assert record.entry_type == "custom_message"


@pytest.mark.parametrize("operation", ["acquire", "renew", "release"])
def test_runtime_lease_route_uses_harness_neutral_contract(
    operation: RuntimeLeaseOperation,
) -> None:
    route = agent_session_runtime_lease("session-1", operation)

    assert route == f"/api/v1/agent-sessions/session-1/runtime-lease/{operation}/"
    assert "checkpoint-lease" not in route


@pytest.mark.asyncio
async def test_python_client_matches_canonical_provider_and_task_contract():
    requests: list[tuple[str, str, str]] = []
    task = {
        "uid": "task-uid-1",
        "task_id": "task-1",
        "context_id": "context-1",
        "agent_uid": "agent-1",
        "agent_session_uid": "session-1",
        "status": "submitted",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = json.loads(request.content) if request.content else None
        requests.append((request.method, path, request.url.query.decode()))
        if path == "/api/v1/runtime-credentials/token/":
            return httpx.Response(200, json={"access": "runtime-token"})
        if path == "/api/v1/model-provider-credentials/hydrate/":
            return httpx.Response(
                200,
                json={
                    "credentials": {
                        "openai": {
                            "credential_kind": "api_key",
                            "credential": {"api_key": "provider-secret"},
                        }
                    },
                    "provider_control": {
                        "schema_version": 1,
                        "catalog_digest": f"sha256:{'0' * 64}",
                        "provider": "openai",
                        "model": {
                            "model": "gpt-5.4",
                            "api": "openai-responses",
                            "input": ["text", "image"],
                            "reasoning": True,
                            "thinking_levels": [
                                "off",
                                "low",
                                "medium",
                                "high",
                                "xhigh",
                            ],
                        },
                    },
                },
            )
        if path == "/api/v1/agent-tasks/" and request.method == "POST":
            return httpx.Response(201, json=task)
        if path == "/api/v1/agent-tasks/" and request.method == "GET":
            return httpx.Response(200, json={"results": [task]})
        if path == "/api/v1/agent-tasks/task-uid-1/":
            return httpx.Response(200, json={**task, "status": "working"})
        if path == "/api/v1/agent-tasks/task-uid-1/snapshot/":
            return httpx.Response(
                200,
                json={"task": {**task, "status": "working"}, "event_cursor": 3},
            )
        if path == "/api/v1/agent-tasks/task-uid-1/events/":
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "sequence": 3,
                            "event_type": "status_changed",
                            "status": "working",
                        }
                    ],
                    "next_cursor": 3,
                    "has_more": False,
                },
            )
        if path == "/api/v1/agent-tasks/task-uid-1/dispatches/":
            return httpx.Response(
                200,
                json=[
                    {
                        "uid": "dispatch-1",
                        "state": "pending",
                        "current_attempt_uid": None,
                    }
                ],
            )
        if path == "/api/v1/agent-tasks/task-uid-1/dispatches/claim/":
            assert payload == {
                "holder_id": "holder-1",
                "lease_token": "lease-1",
                "dispatch_uid": "dispatch-1",
                "executor_runtime_id": "",
                "executor_instance_id": "ms-tau-1",
            }
            return httpx.Response(
                201,
                json={
                    "uid": "attempt-1",
                    "dispatch_uid": "dispatch-1",
                    "attempt_number": 1,
                    "state": "claimed",
                },
            )
        if path == "/api/v1/agent-tasks/task-uid-1/attempts/start/":
            assert payload == {
                "attempt_uid": "attempt-1",
                "holder_id": "holder-1",
                "lease_token": "lease-1",
            }
            return httpx.Response(
                200,
                json={
                    "uid": "attempt-1",
                    "dispatch_uid": "dispatch-1",
                    "attempt_number": 1,
                    "state": "running",
                },
            )
        if path == "/api/v1/agent-tasks/task-uid-1/outputs/create/":
            assert payload == {
                "attempt_uid": "attempt-1",
                "holder_id": "holder-1",
                "lease_token": "lease-1",
                "artifact_id": "artifact-1",
                "parts": [{"text": "answer"}],
                "name": "",
                "description": "",
            }
            return httpx.Response(201, json={"uid": "output-1", "revision": 1})
        if path == "/api/v1/agent-tasks/task-uid-1/outputs/append/":
            assert payload == {
                "attempt_uid": "attempt-1",
                "holder_id": "holder-1",
                "lease_token": "lease-1",
                "output_uid": "output-1",
                "expected_revision": 1,
                "parts": [{"text": " continues"}],
                "last_chunk": False,
            }
            return httpx.Response(200, json={"uid": "output-1", "revision": 2})
        if path == "/api/v1/agent-tasks/task-uid-1/outputs/finalize/":
            assert payload == {
                "attempt_uid": "attempt-1",
                "holder_id": "holder-1",
                "lease_token": "lease-1",
                "output_uid": "output-1",
                "expected_revision": 2,
            }
            return httpx.Response(200, json={"uid": "output-1", "revision": 3})
        if path == "/api/v1/agent-tasks/task-uid-1/attempts/settle/":
            assert payload == {
                "attempt_uid": "attempt-1",
                "holder_id": "holder-1",
                "lease_token": "lease-1",
                "status": "completed",
            }
            return httpx.Response(
                200,
                json={
                    "uid": "attempt-1",
                    "dispatch_uid": "dispatch-1",
                    "attempt_number": 1,
                    "state": "completed",
                },
            )
        if path == "/api/v1/agent-tasks/task-uid-1/continue/":
            return httpx.Response(200, json=task)
        if path == "/api/v1/agent-tasks/task-uid-1/cancel/":
            return httpx.Response(200, json={**task, "status": "canceled"})
        return httpx.Response(404)

    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
    )
    async with httpx.AsyncClient(
        base_url=settings.backend_url,
        transport=httpx.MockTransport(handler),
    ) as http:
        client = MainSequenceClient(
            settings,
            RuntimeCredentialAuth(settings, exchange_client=http),
            client=http,
        )
        evidence = await client.hydrate_provider_credential(
            "openai",
            model="gpt-5.4",
            session_uid="session-1",
            holder_id="ms-tau-1",
        )
        created = await client.create_task({"task_id": "task-1"})
        found = await client.get_task_by_protocol_id("task-1")
        loaded = await client.get_task("task-uid-1")
        snapshot = await client.get_task_snapshot("task-uid-1")
        events = await client.list_task_events(
            "task-uid-1",
            after_sequence=2,
        )
        dispatches = await client.list_task_dispatches("task-uid-1")
        attempt = await client.claim_task_dispatch(
            "task-uid-1",
            holder_id="holder-1",
            lease_token="lease-1",
            dispatch_uid="dispatch-1",
            executor_instance_id="ms-tau-1",
        )
        started = await client.start_task_attempt(
            "task-uid-1",
            attempt_uid="attempt-1",
            holder_id="holder-1",
            lease_token="lease-1",
        )
        output = await client.create_task_output(
            "task-uid-1",
            attempt_uid="attempt-1",
            holder_id="holder-1",
            lease_token="lease-1",
            artifact_id="artifact-1",
            parts=[{"text": "answer"}],
        )
        appended = await client.append_task_output(
            "task-uid-1",
            attempt_uid="attempt-1",
            holder_id="holder-1",
            lease_token="lease-1",
            output_uid="output-1",
            expected_revision=1,
            parts=[{"text": " continues"}],
        )
        finalized = await client.finalize_task_output(
            "task-uid-1",
            attempt_uid="attempt-1",
            holder_id="holder-1",
            lease_token="lease-1",
            output_uid="output-1",
            expected_revision=2,
        )
        settled = await client.settle_task_attempt(
            "task-uid-1",
            attempt_uid="attempt-1",
            holder_id="holder-1",
            lease_token="lease-1",
            status="completed",
        )
        continued = await client.continue_task(
            "task-uid-1",
            {"message_id": "message-2"},
        )
        cancelled = await client.cancel_task("task-uid-1")

    assert evidence.credential.secret() == "provider-secret"
    assert evidence.provider_control.model.model == "gpt-5.4"
    assert created.created is True
    assert created.task.uid == found.uid == "task-uid-1"
    assert loaded.status == "working"
    assert snapshot.event_cursor == 3
    assert events.events[0].sequence == 3
    assert dispatches[0].uid == "dispatch-1"
    assert attempt.uid == "attempt-1"
    assert started.state == "running"
    assert output == {"uid": "output-1", "revision": 1}
    assert appended == {"uid": "output-1", "revision": 2}
    assert finalized == {"uid": "output-1", "revision": 3}
    assert settled.state == "completed"
    assert continued.status == "submitted"
    assert cancelled.status == "canceled"
    assert requests == [
        ("POST", "/api/v1/runtime-credentials/token/", ""),
        ("POST", "/api/v1/model-provider-credentials/hydrate/", ""),
        ("POST", "/api/v1/agent-tasks/", ""),
        ("GET", "/api/v1/agent-tasks/", "task_id=task-1"),
        ("GET", "/api/v1/agent-tasks/task-uid-1/", ""),
        ("GET", "/api/v1/agent-tasks/task-uid-1/snapshot/", ""),
        ("GET", "/api/v1/agent-tasks/task-uid-1/events/", "after_sequence=2&limit=100"),
        ("GET", "/api/v1/agent-tasks/task-uid-1/dispatches/", ""),
        ("POST", "/api/v1/agent-tasks/task-uid-1/dispatches/claim/", ""),
        ("POST", "/api/v1/agent-tasks/task-uid-1/attempts/start/", ""),
        ("POST", "/api/v1/agent-tasks/task-uid-1/outputs/create/", ""),
        ("POST", "/api/v1/agent-tasks/task-uid-1/outputs/append/", ""),
        ("POST", "/api/v1/agent-tasks/task-uid-1/outputs/finalize/", ""),
        ("POST", "/api/v1/agent-tasks/task-uid-1/attempts/settle/", ""),
        ("POST", "/api/v1/agent-tasks/task-uid-1/continue/", ""),
        ("POST", "/api/v1/agent-tasks/task-uid-1/cancel/", ""),
    ]


@pytest.mark.asyncio
async def test_idempotent_session_get_retries_transient_backend_failure():
    session_uid = "session-1"
    session_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_attempts
        if request.url.path == "/api/v1/runtime-credentials/token/":
            return httpx.Response(200, json={"access": "runtime-token"})
        if request.url.path == f"/api/v1/agent-sessions/{session_uid}/":
            session_attempts += 1
            if session_attempts == 1:
                return httpx.Response(500, json={"detail": "database unavailable"})
            return httpx.Response(
                200,
                json={
                    "uid": session_uid,
                    "harness": "tau",
                    "harness_protocol": "tau-session-v1",
                    "harness_version": "0.4.2",
                    "llm_provider": "openai",
                    "llm_model": "gpt-5.4",
                },
            )
        return httpx.Response(404)

    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
    )
    async with httpx.AsyncClient(
        base_url=settings.backend_url,
        transport=httpx.MockTransport(handler),
    ) as http:
        auth = RuntimeCredentialAuth(settings, exchange_client=http)
        client = MainSequenceClient(settings, auth, client=http)
        with patch("ms_tau_sdk.backend.client.asyncio.sleep", AsyncMock()) as sleep:
            session = await client.get_session(session_uid)

    assert session.uid == session_uid
    assert session_attempts == 2
    sleep.assert_awaited_once_with(0.25)


@pytest.mark.asyncio
async def test_session_get_rejects_missing_harness_contract():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/runtime-credentials/token/":
            return httpx.Response(200, json={"access": "runtime-token"})
        return httpx.Response(200, json={"uid": "session-1"})

    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
    )
    async with httpx.AsyncClient(
        base_url=settings.backend_url,
        transport=httpx.MockTransport(handler),
    ) as http:
        auth = RuntimeCredentialAuth(settings, exchange_client=http)
        client = MainSequenceClient(settings, auth, client=http)
        with pytest.raises(
            BackendError,
            match=("required harness contract: harness, harness_protocol, harness_version"),
        ):
            await client.get_session("session-1")


@pytest.mark.asyncio
async def test_session_get_rejects_contradictory_harness_protocol():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/runtime-credentials/token/":
            return httpx.Response(200, json={"access": "runtime-token"})
        return httpx.Response(
            200,
            json={
                "uid": "session-1",
                "harness": "tau",
                "harness_protocol": "pi-checkpoint-v1",
                "harness_version": "0.4.2",
            },
        )

    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
    )
    async with httpx.AsyncClient(
        base_url=settings.backend_url,
        transport=httpx.MockTransport(handler),
    ) as http:
        auth = RuntimeCredentialAuth(settings, exchange_client=http)
        client = MainSequenceClient(settings, auth, client=http)
        with pytest.raises(BackendError, match="required harness contract"):
            await client.get_session("session-1")


@pytest.mark.asyncio
async def test_rejected_backend_call_logs_safe_structured_error_evidence():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/runtime-credentials/token/":
            return httpx.Response(200, json={"access": "runtime-token"})
        return httpx.Response(
            400,
            json={
                "error_code": "runtime_entry_invalid",
                "error_detail": "Tau session entry is invalid.",
                "field_errors": {
                    "entries": {"0": {"entry": ["timing is not allowed"]}}
                },
            },
        )

    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
    )
    async with httpx.AsyncClient(
        base_url=settings.backend_url,
        transport=httpx.MockTransport(handler),
    ) as http:
        auth = RuntimeCredentialAuth(settings, exchange_client=http)
        client = MainSequenceClient(settings, auth, client=http)
        with patch("ms_tau_sdk.backend.client.logger.info") as log_info:
            with pytest.raises(BackendError):
                await client.get_session("session-1")

    rejected = next(
        call
        for call in log_info.call_args_list
        if call.kwargs.get("status_code") == 400
    )
    assert rejected.kwargs["backend_error_code"] == "runtime_entry_invalid"
    assert rejected.kwargs["backend_error_detail"] == "Tau session entry is invalid."
    assert rejected.kwargs["backend_field_error_paths"] == ["entries.0.entry.0"]
    assert "timing is not allowed" not in str(rejected)
