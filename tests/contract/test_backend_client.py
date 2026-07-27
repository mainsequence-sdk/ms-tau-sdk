import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from astro.backend.auth import RuntimeCredentialAuth
from astro.backend.client import MainSequenceClient
from astro.backend.models import (
    RuntimeLeaseReleaseRequest,
    RuntimeLeaseRenewRequest,
    RuntimeLeaseRequest,
    RuntimeStatePatch,
    SessionEntryAppendRequest,
)
from astro.errors import BackendError
from astro.settings import Settings


@pytest.mark.asyncio
async def test_python_client_matches_existing_django_session_contract():
    session_uid = "session-1"
    requests: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = json.loads(request.content) if request.content else None
        requests.append((request.method, path, payload))
        if path.endswith("/runtime-credentials/token/"):
            return httpx.Response(200, json={"access": "runtime-token"})
        if path.endswith(f"/sessions/{session_uid}/") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "uid": session_uid,
                    "agent_uid": "agent-1",
                    "harness": "tau",
                    "harness_protocol": "tau-session-v1",
                    "harness_version": "0.3.1",
                    "llm_provider": "openai",
                    "llm_model": "gpt-5.4",
                    "llm_thinking": "high",
                    "created_by_user_uid": "user-1",
                },
            )
        if path.endswith(f"/sessions/{session_uid}/") and request.method == "PATCH":
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
                    "harness_version": "0.3.1",
                    **payload,
                    "created_by_user_uid": "user-1",
                },
            )
        if path.endswith(f"/sessions/{session_uid}/agent-card/"):
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "agent_uid": "agent-1",
                    "agent_card": {"name": "Astro"},
                },
            )
        if path.endswith(f"/sessions/{session_uid}/capabilities/"):
            return httpx.Response(
                200,
                json=[
                    {
                        "uid": "binding-1",
                        "capability_uid": "capability-1",
                        "source_type": "repository",
                        "capability": {
                            "uid": "capability-1",
                            "kind": "skill",
                            "source_type": "repository",
                        },
                    }
                ],
            )
        if path.endswith("/capabilities/capability-1/content/"):
            return httpx.Response(
                200,
                json={
                    "content": "# Skill",
                    "content_sha256": "sha256:test",
                    "content_mime_type": "text/markdown",
                    "content_size": 7,
                },
            )
        if (
            path.endswith(f"/sessions/{session_uid}/entries/")
            and request.method == "GET"
        ):
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
        if path.endswith(f"/sessions/{session_uid}/entries/append/"):
            assert payload == {
                "lease_token": "lease-token",
                "expected_sequence": 1,
                "idempotency_key": "entry-2",
                "entry": {
                    "id": "entry-2",
                    "parent_id": "entry-1",
                    "timestamp": 1784851201.0,
                    "type": "label",
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
        if path.endswith(f"/sessions/{session_uid}/checkpoint_lease/acquire/"):
            assert payload == {
                "holder_id": "astro-1",
                "ttl_seconds": 90,
                "lease_purpose": "runtime_run",
            }
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "holder_id": "astro-1",
                    "lease_token": "lease-token",
                    "lease_expires_at": "2026-07-24T00:00:00Z",
                    "checkpoint_version": 0,
                    "bundle_hash": "",
                    "lease_purpose": "runtime_run",
                    "agent_session_status": "running",
                    "working": True,
                    "cancel_requested": False,
                    "cancellation": None,
                },
            )
        if path.endswith(f"/sessions/{session_uid}/checkpoint_lease/renew/"):
            assert payload == {
                "lease_token": "lease-token",
                "holder_id": "astro-1",
                "ttl_seconds": 90,
                "lease_purpose": "runtime_run",
            }
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "holder_id": payload["holder_id"],
                    "lease_token": payload["lease_token"],
                    "lease_expires_at": "2026-07-24T00:01:00Z",
                    "checkpoint_version": 0,
                    "bundle_hash": "",
                    "lease_purpose": "runtime_run",
                    "agent_session_status": "running",
                    "working": True,
                    "cancel_requested": False,
                    "cancellation": None,
                },
            )
        if path.endswith(f"/sessions/{session_uid}/checkpoint_lease/release/"):
            assert payload == {
                "lease_token": "lease-token",
                "holder_id": "astro-1",
                "reason": "runtime_eviction",
            }
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "released": True,
                    "checkpoint_version": 0,
                    "bundle_hash": "",
                },
            )
        if (
            path.endswith(f"/sessions/{session_uid}/runtime_state/")
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
                    "harness_version": "0.3.1",
                    **payload,
                    "status": "running",
                    "runtime_state": "working",
                    "working": True,
                },
            )
        if path.endswith(f"/sessions/{session_uid}/runtime_state/"):
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "harness": "tau",
                    "harness_protocol": "tau-session-v1",
                    "harness_version": "0.3.1",
                    "status": "running",
                    "runtime_state": "working",
                    "working": True,
                },
            )
        if path.endswith(f"/sessions/{session_uid}/runtime_cancel_request/"):
            assert payload == {
                "reason": "user_requested",
                "message": "stop",
                "requested_by_holder_id": "astro-1",
            }
            return httpx.Response(
                200,
                json={
                    "agent_session_uid": session_uid,
                    "harness": "tau",
                    "harness_protocol": "tau-session-v1",
                    "harness_version": "0.3.1",
                    "status": "running",
                    "runtime_state": "working",
                    "working": True,
                    "cancel_state": "requested",
                    "cancel_requested": True,
                    "cancellation_id": "00000000-0000-4000-8000-000000000001",
                },
            )
        return httpx.Response(404)

    settings = Settings(
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
        capabilities = await client.list_session_capabilities(session_uid)
        content = await client.get_capability_content("capability-1")
        entries = await client.get_entries(session_uid)
        updated = await client.update_session_config(
            session_uid,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            thinking_level="high",
        )
        lease = await client.acquire_runtime_lease(
            session_uid,
            RuntimeLeaseRequest(holder_id="astro-1", ttl_seconds=90),
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
                    "label": "Updated label",
                },
            ),
        )
        cancelled = await client.request_runtime_cancel(
            session_uid,
            message="stop",
            requested_by_holder_id="astro-1",
        )
        renewed = await client.renew_runtime_lease(
            session_uid,
            RuntimeLeaseRenewRequest(
                lease_token=lease.lease_token,
                holder_id="astro-1",
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
                holder_id="astro-1",
                reason="runtime_eviction",
            ),
        )

    assert session.harness == "tau"
    assert session.active_provider == "openai"
    assert session.active_model == "gpt-5.4"
    assert session.active_thinking == "high"
    assert card.agent_card == {"name": "Astro"}
    assert capabilities[0].capability.uid == "capability-1"
    assert content.content == "# Skill"
    assert entries.next_sequence == 1
    assert entries.entries[0].entry_json["label"] == "Session label"
    assert appended.sequence == 1
    assert updated.active_provider == "anthropic"
    assert updated.active_model == "claude-sonnet-4-20250514"
    assert cancelled.cancel_state == "requested"
    assert renewed.checkpoint_version == 0
    assert state.runtime_state == "working"
    assert patched_state.active_provider == "openai"
    assert requests[0][1].endswith("/runtime-credentials/token/")


@pytest.mark.asyncio
async def test_idempotent_session_get_retries_transient_backend_failure():
    session_uid = "session-1"
    session_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_attempts
        if request.url.path.endswith("/runtime-credentials/token/"):
            return httpx.Response(200, json={"access": "runtime-token"})
        if request.url.path.endswith(f"/sessions/{session_uid}/"):
            session_attempts += 1
            if session_attempts == 1:
                return httpx.Response(500, json={"detail": "database unavailable"})
            return httpx.Response(
                200,
                json={
                    "uid": session_uid,
                    "harness": "tau",
                    "harness_protocol": "tau-session-v1",
                    "harness_version": "0.3.1",
                    "llm_provider": "openai",
                    "llm_model": "gpt-5.4",
                },
            )
        return httpx.Response(404)

    settings = Settings(
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
        with patch("astro.backend.client.asyncio.sleep", AsyncMock()) as sleep:
            session = await client.get_session(session_uid)

    assert session.uid == session_uid
    assert session_attempts == 2
    sleep.assert_awaited_once_with(0.25)


@pytest.mark.asyncio
async def test_session_get_rejects_missing_harness_contract():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runtime-credentials/token/"):
            return httpx.Response(200, json={"access": "runtime-token"})
        return httpx.Response(200, json={"uid": "session-1"})

    settings = Settings(
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
            match=(
                "required harness contract: harness, harness_protocol, "
                "harness_version"
            ),
        ):
            await client.get_session("session-1")


@pytest.mark.asyncio
async def test_session_get_rejects_contradictory_harness_protocol():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runtime-credentials/token/"):
            return httpx.Response(200, json={"access": "runtime-token"})
        return httpx.Response(
            200,
            json={
                "uid": "session-1",
                "harness": "tau",
                "harness_protocol": "pi-checkpoint-v1",
                "harness_version": "0.3.1",
            },
        )

    settings = Settings(
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
