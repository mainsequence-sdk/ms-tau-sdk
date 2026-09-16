import httpx

from ms_tau_sdk.backend.auth import RuntimeCredentialAuth
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.settings import TauSDKSettings


async def test_task_creation_preserves_backend_replay_signal():
    task = {
        "uid": "task-uid-1",
        "task_id": "task-1",
        "context_id": "session-1",
        "agent_uid": "agent-1",
        "status": "completed",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/runtime-credentials/token/":
            return httpx.Response(200, json={"access": "runtime-token"})
        if request.url.path == "/api/v1/agent-tasks/":
            return httpx.Response(200, json=task)
        return httpx.Response(404)

    config = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend.test",
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
    )
    async with httpx.AsyncClient(
        base_url=config.backend_url,
        transport=httpx.MockTransport(handler),
    ) as http:
        client = MainSequenceClient(
            config,
            RuntimeCredentialAuth(config, exchange_client=http),
            client=http,
        )
        replay = await client.create_task({"task_id": "task-1"})

    assert replay.created is False
    assert replay.task.status == "completed"
