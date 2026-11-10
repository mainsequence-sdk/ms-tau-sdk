"""Local model changes stay attached to the same durable session."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from tau_agent.messages import AssistantMessage
from tau_agent.provider_events import AssistantDoneEvent, TextDeltaEvent
from tau_ai.fake import FakeProvider

from ms_tau_sdk.app import create_app
from ms_tau_sdk.application import ApplicationServices
from ms_tau_sdk.backend.auth import JWTAuth
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


def _local_settings(tmp_path) -> TauSDKSettings:
    return TauSDKSettings(
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


async def _evidence(provider, *, model, thinking_level, holder_id):
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


def _turn(model, answer):
    message = AssistantMessage(model=model, stop_reason="stop", content=answer)
    return [
        TextDeltaEvent(content_index=0, delta=answer, partial=message),
        AssistantDoneEvent(reason="stop", message=message),
    ]


def _local_app(settings, fake_providers, monkeypatch, *, thinking_level="off"):
    remote = Mock(spec=MainSequenceClient)
    remote.auth = Mock()
    remote.hydrate_local_provider_credential = AsyncMock(side_effect=_evidence)
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
            thinking_level=thinking_level,
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
    return create_app(settings, services_factory=lambda _settings: services), runtime


@pytest.mark.asyncio
async def test_local_session_can_change_model_and_reload_with_new_evidence(tmp_path):
    settings = _local_settings(tmp_path)
    remote = Mock(spec=MainSequenceClient)
    remote.auth = Mock()
    remote.hydrate_local_provider_credential = AsyncMock(side_effect=_evidence)
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
    settings = _local_settings(tmp_path)
    fake_providers = {
        "gpt-5.4": FakeProvider([_turn("gpt-5.4", "Before switch")]),
        "gpt-5.5": FakeProvider([_turn("gpt-5.5", "After switch")]),
    }
    app, runtime = _local_app(settings, fake_providers, monkeypatch)

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


@pytest.mark.asyncio
async def test_a_new_chat_reports_the_configured_model_and_starts_on_a_chosen_one(
    asgi_client, monkeypatch, tmp_path
):
    settings = _local_settings(tmp_path)
    fake_providers = {
        "gpt-5.4": FakeProvider([_turn("gpt-5.4", "Configured model")]),
        "gpt-5.5": FakeProvider([_turn("gpt-5.5", "Chosen model")]),
    }
    app, _runtime = _local_app(settings, fake_providers, monkeypatch)

    async with asgi_client(app, lifespan=True) as http:
        configured = await http.get("/api/chat/session-model", params={"sessionUid": "new-chat"})
        chosen = await http.put(
            "/api/chat/session-model",
            json={"sessionUid": "new-chat", "provider": "openai", "model": "gpt-5.5"},
        )
        first = await http.post("/api/chat", json={"sessionUid": "new-chat", "message": "One"})

    uid = settings.local_session_uid("new-chat")
    assert configured.status_code == 200, configured.text
    assert configured.json() == {
        "sessionUid": uid,
        "model": {"provider": "openai", "model": "gpt-5.4", "thinkingLevel": None},
    }
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["sessionUid"] == uid
    assert chosen.json()["model"]["model"] == "gpt-5.5"
    assert first.headers["X-Agent-Session-Uid"] == uid
    assert "Chosen model" in first.text
    assert fake_providers["gpt-5.4"].calls == []


@pytest.mark.asyncio
async def test_chat_reports_the_level_a_session_runs_at(asgi_client, monkeypatch, tmp_path):
    # The provider factory runs a level the model cannot run at a shared one (issue #82).
    settings = _local_settings(tmp_path)
    fake_providers = {"gpt-5.4": FakeProvider([]), "gpt-5.5": FakeProvider([])}
    app, _runtime = _local_app(settings, fake_providers, monkeypatch, thinking_level="low")

    async with asgi_client(app, lifespan=True) as http:
        chosen = await http.put(
            "/api/chat/session-model",
            json={
                "sessionUid": "board-chat",
                "provider": "openai",
                "model": "gpt-5.5",
                "thinkingLevel": "max",
            },
        )
        reported = await http.get("/api/chat/session-model", params={"sessionUid": "board-chat"})

    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["model"]["thinkingLevel"] == "low"
    assert reported.json()["model"]["thinkingLevel"] == "low"


# A local provider credential hydrate address (ADR 0027, amended 2026-11-10)

LOCAL_HYDRATE_ORIGIN = "http://127.0.0.1:8611"


def _endpoint_answer(model: str) -> dict[str, object]:
    return {
        "credentials": {
            "openai": {
                "uid": "endpoint-credential",
                "credential_kind": "api_key",
                "credential": {"type": "api_key", "key": "person-provider-secret"},
            }
        },
        "missing": [],
        "revoked": [],
        "provider_control": {
            "schema_version": 1,
            "catalog_digest": f"sha256:{'0' * 64}",
            "provider": "openai",
            "model": {
                "model": model,
                "api": "openai-responses",
                "input": ["text"],
                "reasoning": False,
                "thinking_levels": [],
            },
        },
    }


@asynccontextmanager
async def _local_address_app(tmp_path, fake_providers, *, answered_model=None):
    """The app with the real client, hydrating from the developer's own loopback service."""

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
        exclude_mainsequence_mcp=True,
        local_provider_credential_hydrate_url=LOCAL_HYDRATE_ORIGIN,
    )
    endpoint_requests: list[httpx.Request] = []
    platform_requests: list[httpx.Request] = []

    def endpoint(request: httpx.Request) -> httpx.Response:
        endpoint_requests.append(request)
        selected = json.loads(request.content)["execution_selection"]["model"]
        return httpx.Response(200, json=_endpoint_answer(answered_model or selected))

    def platform(request: httpx.Request) -> httpx.Response:
        platform_requests.append(request)
        return httpx.Response(500)

    async with (
        httpx.AsyncClient(
            base_url=settings.backend_url, transport=httpx.MockTransport(platform)
        ) as http,
        httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as hydrate_http,
    ):
        remote = MainSequenceClient(
            settings, JWTAuth(settings), client=http, hydrate_client=hydrate_http
        )
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
        app = create_app(settings, services_factory=lambda _settings: services)
        yield app, settings, endpoint_requests, platform_requests


@pytest.mark.asyncio
async def test_model_selection_hydrates_from_a_local_address_for_its_session(asgi_client, tmp_path):
    fake_providers = {
        "gpt-5.4": FakeProvider([]),
        "gpt-5.5": FakeProvider([_turn("gpt-5.5", "Chosen model")]),
    }
    async with _local_address_app(tmp_path, fake_providers) as (app, settings, sent, platform):
        async with asgi_client(app, lifespan=True) as http:
            chosen = await http.put(
                "/api/chat/session-model",
                json={"sessionUid": "new-chat", "provider": "openai", "model": "gpt-5.5"},
            )
            first = await http.post("/api/chat", json={"sessionUid": "new-chat", "message": "One"})
            selected = await http.get("/api/chat/session-model", params={"sessionUid": "new-chat"})

    uid = settings.local_session_uid("new-chat")
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["sessionUid"] == uid
    assert selected.json()["sessionUid"] == uid
    assert selected.json()["model"]["model"] == "gpt-5.5"
    assert "Chosen model" in first.text
    assert platform == []
    bodies = [json.loads(request.content) for request in sent]
    assert {body["agent_session_uid"] for body in bodies} == {uid}
    assert bodies[-1]["execution_selection"] == {"provider": "openai", "model": "gpt-5.5"}
    assert all("authorization" not in request.headers for request in sent)
    assert all(str(request.url).startswith(LOCAL_HYDRATE_ORIGIN) for request in sent)


@pytest.mark.asyncio
async def test_a_selection_the_local_address_does_not_project_is_refused(asgi_client, tmp_path):
    fake_providers = {"gpt-5.4": FakeProvider([]), "gpt-5.5": FakeProvider([])}
    async with _local_address_app(tmp_path, fake_providers, answered_model="gpt-5.4") as (
        app,
        settings,
        sent,
        _platform,
    ):
        async with asgi_client(app, lifespan=True) as http:
            refused = await http.put(
                "/api/chat/session-model",
                json={"sessionUid": "new-chat", "provider": "openai", "model": "gpt-5.5"},
            )
            selected = await http.get("/api/chat/session-model", params={"sessionUid": "new-chat"})

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"] == "backend_conflict"
    assert "person-provider-secret" not in refused.text
    assert selected.json()["model"]["model"] == "gpt-5.4"
    assert {json.loads(request.content)["agent_session_uid"] for request in sent} == {
        settings.local_session_uid("new-chat")
    }
