import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tau_agent.messages import AssistantMessage
from tau_agent.provider_events import AssistantDoneEvent, TextDeltaEvent
from tau_ai.fake import FakeProvider

from ms_tau_sdk.app import create_app
from ms_tau_sdk.application import ApplicationServices
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.local import LocalDevelopmentBackend
from ms_tau_sdk.backend.models import (
    ProviderControl,
    ProviderCredential,
    ProviderExecutionEvidence,
)
from ms_tau_sdk.providers.factory import ProviderFactory, ProviderRuntime
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings


async def test_first_local_chat_reaches_provider_execution(
    asgi_client,
    monkeypatch,
    tmp_path,
):
    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        local_state_root=tmp_path / "state",
        state_root=tmp_path / "tau-state",
        backend_url="http://backend.test",
        auth_mode="jwt",
        local_mode=True,
        access_token="mainsequence-access-token",
        refresh_token="mainsequence-refresh-token",
        local_provider="openai",
        local_model="gpt-5.4",
        startup_dependencies_enabled=False,
    )
    evidence = ProviderExecutionEvidence(
        credential=ProviderCredential(
            provider="openai",
            credential_kind="api_key",
            api_key="provider-secret",
        ),
        provider_control=ProviderControl(
            schema_version=1,
            catalog_digest=f"sha256:{'0' * 64}",
            provider="openai",
            model={
                "model": "gpt-5.4",
                "api": "openai-responses",
                "input": ["text"],
                "reasoning": False,
                "thinking_levels": [],
            },
        ),
    )
    remote = Mock(spec=MainSequenceClient)
    remote.auth = Mock()
    remote.hydrate_local_provider_credential = AsyncMock(return_value=evidence)
    remote.aclose = AsyncMock()
    backend = LocalDevelopmentBackend(settings, remote)

    def provider_turn(text: str):
        return [
            TextDeltaEvent(
                content_index=0,
                delta=text,
                partial=AssistantMessage(
                    model="gpt-5.4",
                    stop_reason="stop",
                    content=text,
                ),
            ),
            AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(
                    model="gpt-5.4",
                    stop_reason="stop",
                    content=text,
                ),
            ),
        ]

    fake_provider = FakeProvider(
        [
            provider_turn("Local provider execution succeeded."),
            provider_turn("Local A2A message succeeded."),
            provider_turn("Local A2A Task succeeded."),
            provider_turn("Local A2A stream succeeded."),
            provider_turn("Local A2A JSON-RPC succeeded."),
            provider_turn("Local A2A continuation succeeded."),
        ]
    )
    providers = Mock(spec=ProviderFactory)
    providers.for_session_credential.return_value = ProviderRuntime(
        name="openai",
        model="gpt-5.4",
        thinking_level="off",
        provider=fake_provider,
        credential=evidence.credential,
        provider_control=evidence.provider_control,
    )
    runtime = SessionRuntimeManager(
        settings=settings,
        backend=backend,
        providers=providers,
    )
    services = ApplicationServices(
        settings=settings,
        auth=remote.auth,
        backend=backend,
        providers=providers,
        runtime=runtime,
    )
    mcp_client = SimpleNamespace(tools=(), resources=(), aclose=AsyncMock())
    connect_mcp = AsyncMock(return_value=mcp_client)
    monkeypatch.setattr(
        "ms_tau_sdk.runtime.manager.MainSequenceMCPClient.connect",
        connect_mcp,
    )
    app = create_app(settings, services_factory=lambda _settings: services)

    async with asgi_client(app, lifespan=True) as http:
        response = await http.post(
            "/api/chat",
            json={
                "sessionUid": "tutorial-helper",
                "message": "Explain the tutorial updaters.",
            },
        )
        a2a_message = await http.post(
            "/api/a2a/v1/message:send",
            json={
                "message": {
                    "messageId": "local-message-1",
                    "contextId": "local-a2a",
                    "parts": [{"text": "Answer over A2A."}],
                }
            },
        )
        a2a_task = await http.post(
            "/api/a2a/v1/message:send",
            headers={"A2A-Extensions": ("https://mainsequence.ai/a2a/extensions/response-kind/v1")},
            json={
                "taskId": "local-task-1",
                "message": {
                    "messageId": "local-message-2",
                    "contextId": "local-a2a-task",
                    "parts": [{"text": "Run a local A2A Task."}],
                },
                "configuration": {"responseKind": "task"},
            },
        )
        listed_tasks = await http.get(
            "/api/a2a/v1/tasks",
            params={"contextId": "local-a2a-task"},
        )
        fetched_task = await http.get("/api/a2a/v1/tasks/local-task-1")
        a2a_stream = await http.post(
            "/api/a2a/v1/message:stream",
            json={
                "taskId": "local-stream-1",
                "message": {
                    "messageId": "local-message-3",
                    "contextId": "local-a2a-stream",
                    "parts": [{"text": "Stream a local A2A Task."}],
                },
            },
        )
        a2a_rpc = await http.post(
            "/api/a2a/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "local-rpc-1",
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": "local-message-4",
                        "contextId": "local-a2a-rpc",
                        "parts": [{"text": "Answer over local JSON-RPC."}],
                    }
                },
            },
        )
        continuation_context = settings.local_session_uid("local-a2a-continuation")
        await runtime.get(continuation_context)
        continuation_creation = await backend.create_task(
            {
                "task_id": "local-continuation-1",
                "context_id": continuation_context,
                "initial_message": {
                    "message_id": "local-message-5",
                    "role": "user",
                    "parts": [{"text": "Ask for input."}],
                },
            }
        )
        continuation_fence = await runtime.task_execution_fence(continuation_context)
        continuation_dispatch = (
            await backend.list_task_dispatches(continuation_creation.task.uid)
        )[0]
        continuation_attempt = await backend.claim_task_dispatch(
            continuation_creation.task.uid,
            holder_id=continuation_fence.holder_id,
            lease_token=continuation_fence.lease_token,
            dispatch_uid=continuation_dispatch.uid,
        )
        continuation_attempt = await backend.start_task_attempt(
            continuation_creation.task.uid,
            attempt_uid=continuation_attempt.uid,
            holder_id=continuation_fence.holder_id,
            lease_token=continuation_fence.lease_token,
        )
        await backend.settle_task_attempt(
            continuation_creation.task.uid,
            attempt_uid=continuation_attempt.uid,
            holder_id=continuation_fence.holder_id,
            lease_token=continuation_fence.lease_token,
            status="input_required",
            status_message={"message": "Provide input."},
        )
        continued_task = await http.post(
            "/api/a2a/v1/message:send",
            headers={"A2A-Extensions": ("https://mainsequence.ai/a2a/extensions/response-kind/v1")},
            json={
                "message": {
                    "messageId": "local-message-6",
                    "taskId": "local-continuation-1",
                    "contextId": "local-a2a-continuation",
                    "parts": [{"text": "Continue with this input."}],
                },
                "configuration": {"responseKind": "task"},
            },
        )
        subscription_creation = await backend.create_task(
            {
                "task_id": "local-subscription-1",
                "context_id": "local-subscription",
                "initial_message": {
                    "message_id": "local-message-7",
                    "role": "user",
                    "parts": [{"text": "Wait for cancellation."}],
                },
            }
        )
        subscription = asyncio.create_task(
            http.get("/api/a2a/v1/tasks/local-subscription-1:subscribe")
        )
        await asyncio.sleep(0.05)
        canceled_task = await http.post("/api/a2a/v1/tasks/local-subscription-1:cancel")
        subscription_result = await subscription

    assert response.status_code == 200
    assert '"textDelta":"Local provider execution succeeded."' in response.text
    assert '"type":"error"' not in response.text
    assert a2a_message.status_code == 200
    assert a2a_message.json()["message"]["parts"] == [{"text": "Local A2A message succeeded."}]
    assert a2a_task.status_code == 200
    assert a2a_task.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert listed_tasks.status_code == 200
    assert [task["id"] for task in listed_tasks.json()["tasks"]] == ["local-task-1"]
    assert fetched_task.status_code == 200
    assert fetched_task.json()["task"]["artifacts"][0]["parts"] == [
        {"text": "Local A2A Task succeeded."}
    ]
    assert a2a_stream.status_code == 200
    assert a2a_stream.headers["content-type"].startswith("text/event-stream")
    assert "Local A2A stream succeeded." in a2a_stream.text
    assert "TASK_STATE_COMPLETED" in a2a_stream.text
    assert a2a_rpc.status_code == 200
    assert a2a_rpc.json()["result"]["message"]["parts"] == [
        {"text": "Local A2A JSON-RPC succeeded."}
    ]
    assert continued_task.status_code == 200
    assert continued_task.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert subscription_creation.created is True
    assert canceled_task.status_code == 200
    assert canceled_task.json()["task"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert subscription_result.status_code == 200
    assert "TASK_STATE_SUBMITTED" in subscription_result.text
    assert "TASK_STATE_CANCELED" in subscription_result.text
    assert len(fake_provider.calls) == 6
    assert remote.hydrate_local_provider_credential.await_count == 6
    hydrated = providers.for_session_credential.call_args.kwargs["evidence"].credential
    assert hydrated.provider == "openai"
    assert hydrated.secret() == "provider-secret"
    connect_mcp.assert_awaited_once_with(settings=settings, auth=remote.auth)
