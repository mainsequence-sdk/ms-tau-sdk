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


async def test_batch_append_and_tau_activity_match_django_contract():
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
        runtime_credential_secret="credential-secret",
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
