"""Local model changes stay attached to the same durable session."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
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
    TauRuntimeBootstrapRequest,
)
from ms_tau_sdk.providers.factory import ProviderFactory, ProviderRuntime
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings


@pytest.mark.asyncio
async def test_local_session_can_change_model_and_reload_with_new_evidence(tmp_path):
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

    async def evidence(provider, *, model, thinking_level, holder_id):
        del thinking_level, holder_id
        return ProviderExecutionEvidence(
            credential=ProviderCredential(
                provider=provider, credential_kind="api_key", api_key="provider-secret"
            ),
            provider_control=ProviderControl(
                schema_version=1,
                catalog_digest=f"sha256:{'0' * 64}",
                provider=provider,
                model={
                    "model": model,
                    "api": "openai-responses",
                    "input": ["text"],
                    "reasoning": False,
                    "thinking_levels": [],
                },
            ),
        )

    remote = Mock(spec=MainSequenceClient)
    remote.auth = Mock()
    remote.hydrate_local_provider_credential = AsyncMock(side_effect=evidence)
    remote.aclose = AsyncMock()
    backend = LocalDevelopmentBackend(settings, remote)
    uid = settings.local_session_uid("board-chat")
    request = TauRuntimeBootstrapRequest(
        holder_id="test-holder",
        ttl_seconds=60,
        bootstrap_request_uid="first",
        tau_runtime_version="test",
    )

    first = await backend.bootstrap_tau_runtime(uid, request)
    assert first.session.active_model == "gpt-5.4"
    changed = await backend.update_session_config(
        uid, provider="openai", model="gpt-5.5", thinking_level=None
    )
    assert changed.uid == uid
    assert changed.active_model == "gpt-5.5"
    second = await backend.bootstrap_tau_runtime(
        uid, request.model_copy(update={"bootstrap_request_uid": "second"})
    )
    assert second.session.uid == uid
    assert second.session.active_model == "gpt-5.5"
    assert second.provider_control.model.model == "gpt-5.5"
    assert (
        first.session.model_extra["runtime_config_sha256"]
        != second.session.model_extra["runtime_config_sha256"]
    )
    await backend.aclose()


@pytest.mark.asyncio
async def test_model_switch_keeps_chat_session_and_changes_future_provider(
    asgi_client, monkeypatch, tmp_path
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

    async def evidence(provider, *, model, thinking_level, holder_id):
        del thinking_level, holder_id
        return ProviderExecutionEvidence(
            credential=ProviderCredential(
                provider=provider, credential_kind="api_key", api_key="provider-secret"
            ),
            provider_control=ProviderControl(
                schema_version=1,
                catalog_digest=f"sha256:{'0' * 64}",
                provider=provider,
                model={
                    "model": model,
                    "api": "openai-responses",
                    "input": ["text"],
                    "reasoning": False,
                    "thinking_levels": [],
                },
            ),
        )

    def turn(model, answer):
        message = AssistantMessage(model=model, stop_reason="stop", content=answer)
        return [
            TextDeltaEvent(content_index=0, delta=answer, partial=message),
            AssistantDoneEvent(reason="stop", message=message),
        ]

    fake_providers = {
        "gpt-5.4": FakeProvider([turn("gpt-5.4", "Before switch")]),
        "gpt-5.5": FakeProvider([turn("gpt-5.5", "After switch")]),
    }
    remote = Mock(spec=MainSequenceClient)
    remote.auth = Mock()
    remote.hydrate_local_provider_credential = AsyncMock(side_effect=evidence)
    remote.list_model_providers = AsyncMock(
        return_value={"schema_version": 1, "providers": [{"provider": "openai", "models": []}]}
    )
    remote.aclose = AsyncMock()
    backend = LocalDevelopmentBackend(settings, remote)
    providers = Mock(spec=ProviderFactory)

    def runtime_for(session, *, holder_id, evidence):
        del holder_id
        return ProviderRuntime(
            name=session.active_provider,
            model=session.active_model,
            thinking_level="off",
            provider=fake_providers[session.active_model],
            credential=evidence.credential,
            provider_control=evidence.provider_control,
        )

    providers.for_session_credential.side_effect = runtime_for
    runtime = SessionRuntimeManager(settings=settings, backend=backend, providers=providers)
    services = ApplicationServices(
        settings=settings,
        auth=remote.auth,
        backend=backend,
        providers=providers,
        runtime=runtime,
    )
    monkeypatch.setattr(
        "ms_tau_sdk.runtime.manager.MainSequenceMCPClient.connect",
        AsyncMock(return_value=SimpleNamespace(tools=(), resources=(), aclose=AsyncMock())),
    )
    app = create_app(settings, services_factory=lambda _settings: services)

    async with asgi_client(app, lifespan=True) as http:
        catalog = await http.get("/api/chat/model-providers")
        first = await http.post("/api/chat", json={"sessionUid": "board-chat", "message": "One"})
        changed = await http.put(
            "/api/chat/session-model",
            json={"sessionUid": "board-chat", "provider": "openai", "model": "gpt-5.5"},
        )
        second = await http.post("/api/chat", json={"sessionUid": "board-chat", "message": "Two"})
        selected = await http.get("/api/chat/session-model", params={"sessionUid": "board-chat"})
        with pytest.raises(KeyError):
            await runtime.change_session_model(
                settings.local_session_uid("board-chat"),
                provider="openai",
                model="unsupported-model",
                thinking_level=None,
            )
        restored = await http.get("/api/chat/session-model", params={"sessionUid": "board-chat"})

    assert catalog.json()["providers"][0]["provider"] == "openai"
    assert "Before switch" in first.text
    assert changed.status_code == 200, changed.text
    assert changed.json()["sessionUid"] == settings.local_session_uid("board-chat")
    assert "After switch" in second.text
    assert selected.json()["model"]["model"] == "gpt-5.5"
    assert restored.json()["model"]["model"] == "gpt-5.5"
