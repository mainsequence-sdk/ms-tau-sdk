import json

import httpx

from ms_tau_sdk.backend.auth import RuntimeCredentialAuth
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.models import (
    RuntimeActivityPatch,
    SessionEntryBatchAppendRequest,
    SessionEntryBatchItem,
)
from ms_tau_sdk.settings import TauSDKSettings


async def test_batch_append_and_tau_activity_match_platform_contract(runtime_identity_token_file):
    session_uid = "session-1"
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, payload))
        if request.url.path == "/api/v1/runtime-credentials/token/":
            return httpx.Response(200, json={"access": "runtime-token"})
        if request.url.path.endswith("/entries/append-batch/"):
            return httpx.Response(
                201,
                json={
                    "entries": [
                        {
                            "sequence": 4,
                            "entry_type": "label",
                            "entry_json": payload["entries"][0]["entry"],
                            "idempotency_key": "entry-4",
                        }
                    ],
                    "next_sequence": 5,
                    "created_count": 1,
                    "replayed": False,
                },
            )
        if request.url.path.endswith("/tau-runtime-activity/"):
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
                    "runtime_activity": "working",
                    "active_turn_uid": "00000000-0000-0000-0000-000000000004",
                    "activity_revision": 4,
                    "activity_updated_at": "2026-08-27T10:00:00Z",
                },
            )
        return httpx.Response(404)

    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="credential-id",
        runtime_identity_token_file=runtime_identity_token_file,
    )
    http = httpx.AsyncClient(
        base_url=settings.backend_url,
        transport=httpx.MockTransport(handler),
    )
    auth = RuntimeCredentialAuth(settings)
    client = MainSequenceClient(settings, auth, client=http)

    batch = await client.append_entries(
        session_uid,
        SessionEntryBatchAppendRequest(
            lease_token="lease-token",
            expected_sequence=4,
            entries=[
                SessionEntryBatchItem(
                    idempotency_key="entry-4",
                    entry={
                        "id": "entry-4",
                        "type": "label",
                        "label": "Batch",
                    },
                )
            ],
        ),
    )
    activity = await client.patch_runtime_activity(
        session_uid,
        RuntimeActivityPatch(
            holder_id="ms-tau-1",
            lease_token="lease-token",
            expected_activity_revision=3,
            runtime_activity="working",
            active_turn_uid="00000000-0000-0000-0000-000000000004",
        ),
    )

    assert batch.created_count == 1
    assert batch.next_sequence == 5
    assert activity.runtime_activity == "working"
    assert activity.activity_revision == 4
    assert calls[1][1] == (f"/api/v1/agent-sessions/{session_uid}/entries/append-batch/")
    assert calls[2][1] == (f"/api/v1/agent-sessions/{session_uid}/tau-runtime-activity/")
    await http.aclose()


async def test_a_turn_start_presents_the_caller_assertion_and_reads_the_recorded_requester(
    runtime_identity_token_file,
):
    session_uid = "session-1"
    requester_uid = "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d"
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/runtime-credentials/token/":
            return httpx.Response(200, json={"access": "runtime-token"})
        sent.append(request)
        presented = "X-MainSequence-Caller-Assertion" in request.headers
        return httpx.Response(
            200,
            json={
                "agent_session_uid": session_uid,
                "harness": "tau",
                "harness_protocol": "tau-session-v1",
                "harness_version": "0.4.2",
                "runtime_activity": "working",
                "active_turn_uid": "00000000-0000-0000-0000-000000000004",
                "activity_revision": 4,
                "activity_sequence": 1,
                "requester_user_uid": requester_uid if presented else None,
            },
        )

    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="credential-id",
        runtime_identity_token_file=runtime_identity_token_file,
    )
    http = httpx.AsyncClient(
        base_url=settings.backend_url,
        transport=httpx.MockTransport(handler),
    )
    client = MainSequenceClient(settings, RuntimeCredentialAuth(settings), client=http)
    patch = RuntimeActivityPatch(
        holder_id="ms-tau-1",
        lease_token="lease-token",
        activity_sequence=1,
        runtime_activity="working",
        active_turn_uid="00000000-0000-0000-0000-000000000004",
    )

    started = await client.patch_runtime_activity(
        session_uid,
        patch,
        caller_assertion="caller-assertion-jws",
    )
    without = await client.patch_runtime_activity(session_uid, patch)

    assert [request.method for request in sent] == ["PATCH", "PATCH"]
    assert sent[0].headers["X-MainSequence-Caller-Assertion"] == "caller-assertion-jws"
    assert sent[0].headers["Authorization"] == "Bearer runtime-token"
    assert "X-MainSequence-Caller-Assertion" not in sent[1].headers
    # The body is unchanged: the assertion travels only in its header.
    assert json.loads(sent[0].content) == json.loads(sent[1].content)
    assert started.requester_user_uid == requester_uid
    assert without.requester_user_uid is None
    await http.aclose()
