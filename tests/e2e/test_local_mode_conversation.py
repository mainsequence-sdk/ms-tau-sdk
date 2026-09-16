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

    fake_provider = FakeProvider(
        [
            [
                TextDeltaEvent(
                    content_index=0,
                    delta="Local provider execution succeeded.",
                    partial=AssistantMessage(
                        model="gpt-5.4",
                        stop_reason="stop",
                        content="Local provider execution succeeded.",
                    ),
                ),
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(
                        model="gpt-5.4",
                        stop_reason="stop",
                        content="Local provider execution succeeded.",
                    ),
                ),
            ],
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

    assert response.status_code == 200
    assert '"textDelta":"Local provider execution succeeded."' in response.text
    assert '"type":"error"' not in response.text
    assert len(fake_provider.calls) == 1
    remote.hydrate_local_provider_credential.assert_awaited_once()
    hydrated = providers.for_session_credential.call_args.kwargs["evidence"].credential
    assert hydrated.provider == "openai"
    assert hydrated.secret() == "provider-secret"
    connect_mcp.assert_awaited_once_with(settings=settings, auth=remote.auth)
