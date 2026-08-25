import json
from unittest.mock import ANY, AsyncMock, Mock, call

from fastapi import FastAPI
from tau_agent.messages import AssistantMessage

import astro.api.responses as responses_api
from astro.agents import AgentExecutionSnapshot
from astro.api.dependencies import backend, provider_factory, settings
from astro.api.responses import INFERENCE_EXTENSION_URI, router
from astro.app import create_app
from astro.settings import Settings

AGENT_UID = "11111111-1111-4111-8111-111111111111"


def _settings(
    tmp_path,
    *,
    allowed_input_media_types: tuple[str, ...] | None = None,
) -> Settings:
    return Settings(
        _env_file=None,
        sessionless_asset_root=tmp_path,
        agent_execution_snapshot=AgentExecutionSnapshot(
            agent_uid=AGENT_UID,
            name="Research Agent",
            default_provider="openai",
            default_model="gpt-5.1",
            default_thinking="high",
            instructions="Be precise.",
            allowed_input_media_types=(
                allowed_input_media_types
                or ("text/plain", "text/markdown", "application/json", "text/csv")
            ),
        ),
    )


def _app(client: AsyncMock, providers: Mock, runtime_settings: Settings) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[provider_factory] = lambda: providers
    app.dependency_overrides[settings] = lambda: runtime_settings
    return app


def _body(*, strict: bool = False, controls: dict | None = None) -> dict:
    body = {
        "message": {
            "messageId": "message-1",
            "role": "ROLE_USER",
            "parts": [{"text": "Answer this."}],
        },
        "configuration": {
            "acceptedOutputModes": ["application/json" if strict else "text/plain"],
            "responseKind": "message",
        },
    }
    if controls:
        body["metadata"] = {INFERENCE_EXTENSION_URI: controls}
    return body


def _fake_harness(monkeypatch, outputs: list[AssistantMessage]) -> list[object]:
    instances: list[object] = []

    class FakeHarness:
        def __init__(self, config, *, messages=()):
            self.config = config
            self.messages = list(messages)
            self.prompts: list[object] = []
            instances.append(self)

        async def prompt(self, prompt: str):
            self.prompts.append(prompt)
            self.messages.append(outputs.pop(0))
            yield object()

        async def prompt_message(self, prompt):
            self.prompts.append(prompt)
            self.messages.append(outputs.pop(0))
            yield object()

    monkeypatch.setattr(responses_api, "AgentHarness", FakeHarness)
    return instances


def _assistant(text: str, *, stop_reason: str = "stop", error: str | None = None):
    return AssistantMessage(
        content=text,
        api="responses",
        provider="openai",
        model="gpt-5.1",
        stop_reason=stop_reason,
        error_message=error,
    )


async def test_agent_response_uses_snapshot_defaults_without_persistence(
    monkeypatch,
    asgi_client,
    tmp_path,
):
    instances = _fake_harness(monkeypatch, [_assistant("Final answer")])
    client = AsyncMock()
    client.hydrate_provider_credential.return_value = object()
    provider = Mock()
    provider.aclose = AsyncMock()
    providers = Mock()
    providers.validate_selection.return_value = "high"
    providers.build.return_value = provider

    async with asgi_client(_app(client, providers, _settings(tmp_path))) as http:
        response = await http.post(
            f"/api/agents/{AGENT_UID}/responses",
            content=json.dumps(_body()),
            headers={"Content-Type": "application/a2a+json"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/a2a+json")
    message = response.json()["message"]
    assert message["role"] == "ROLE_AGENT"
    assert message["parts"] == [{"text": "Final answer"}]
    assert "contextId" not in message
    resolved = message["metadata"][INFERENCE_EXTENSION_URI]["resolved"]
    assert resolved == {
        "provider": "openai",
        "model": "gpt-5.1",
        "thinking": "high",
        "sources": {
            "provider": "agent_default",
            "model": "agent_default",
            "thinking": "agent_default",
        },
    }
    client.hydrate_provider_credential.assert_awaited_once_with(
        "openai",
        agent_uid=AGENT_UID,
        holder_id=ANY,
    )
    assert client.method_calls == [
        call.hydrate_provider_credential(
            "openai",
            agent_uid=AGENT_UID,
            holder_id=ANY,
        )
    ]
    providers.build.assert_called_once_with(
        client.hydrate_provider_credential.return_value,
        model="gpt-5.1",
        credential_resolver=ANY,
        thinking_level="high",
        max_tokens=4096,
        timeout_seconds=120.0,
    )
    assert instances[0].config.system == "Be precise."
    assert instances[0].prompts[0].content[0].text == "Answer this."
    provider.aclose.assert_awaited_once()


async def test_agent_response_applies_overrides_and_repairs_json(
    monkeypatch,
    asgi_client,
    tmp_path,
):
    instances = _fake_harness(
        monkeypatch,
        [_assistant("not-json"), _assistant('{"answer":42}')],
    )
    client = AsyncMock()
    provider = Mock()
    provider.aclose = AsyncMock()
    providers = Mock()
    providers.validate_selection.return_value = "medium"
    providers.build.return_value = provider
    body = _body(
        strict=True,
        controls={
            "provider": "anthropic",
            "model": "claude-sonnet-4-5",
            "thinking": "medium",
            "maxOutputTokens": 512,
            "timeoutSeconds": 30,
        },
    )

    async with asgi_client(_app(client, providers, _settings(tmp_path))) as http:
        response = await http.post(
            f"/api/agents/{AGENT_UID}/responses",
            content=json.dumps(body),
            headers={"Content-Type": "application/a2a+json"},
        )

    assert response.status_code == 200
    assert response.json()["message"]["parts"] == [
        {"data": {"answer": 42}, "mediaType": "application/json"}
    ]
    providers.validate_selection.assert_called_once_with(
        "anthropic",
        "claude-sonnet-4-5",
        "medium",
    )
    assert "Return only valid JSON" in instances[0].config.system
    assert "Repair this assistant response" in instances[0].prompts[1]


async def test_agent_response_rejects_session_identity_before_hydration(
    asgi_client,
    tmp_path,
):
    client = AsyncMock()
    providers = Mock()
    providers.validate_selection.return_value = "high"
    body = _body()
    body["message"]["contextId"] = "session-1"

    async with asgi_client(_app(client, providers, _settings(tmp_path))) as http:
        response = await http.post(
            f"/api/agents/{AGENT_UID}/responses",
            content=json.dumps(body),
            headers={"Content-Type": "application/a2a+json"},
        )

    assert response.status_code == 400
    client.hydrate_provider_credential.assert_not_awaited()


async def test_agent_response_rejects_second_agent_identity_before_hydration(
    asgi_client,
    tmp_path,
):
    client = AsyncMock()
    providers = Mock()
    providers.validate_selection.return_value = "high"
    body = _body()
    body["agent_uid"] = "22222222-2222-4222-8222-222222222222"

    async with asgi_client(_app(client, providers, _settings(tmp_path))) as http:
        response = await http.post(
            f"/api/agents/{AGENT_UID}/responses",
            content=json.dumps(body),
            headers={"Content-Type": "application/a2a+json"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "agent_uid is not allowed"
    client.hydrate_provider_credential.assert_not_awaited()


async def test_agent_response_stream_emits_only_final_a2a_message(
    monkeypatch,
    asgi_client,
    tmp_path,
):
    _fake_harness(monkeypatch, [_assistant("Final answer")])
    client = AsyncMock()
    provider = Mock()
    provider.aclose = AsyncMock()
    providers = Mock()
    providers.validate_selection.return_value = "high"
    providers.build.return_value = provider

    async with asgi_client(_app(client, providers, _settings(tmp_path))) as http:
        response = await http.post(
            f"/api/agents/{AGENT_UID}/responses/stream",
            content=json.dumps(_body()),
            headers={"Content-Type": "application/a2a+json"},
        )

    assert response.status_code == 200
    payload = json.loads(response.text.removeprefix("data: ").strip())
    assert payload["final"] is True
    assert payload["message"]["parts"] == [{"text": "Final answer"}]
    assert "task" not in payload


async def test_agent_response_accepts_allowed_native_image_and_cleans_it_up(
    monkeypatch,
    asgi_client,
    tmp_path,
):
    instances = _fake_harness(monkeypatch, [_assistant("Image analyzed")])
    client = AsyncMock()
    provider = Mock()
    provider.aclose = AsyncMock()
    providers = Mock()
    providers.validate_selection.return_value = "high"
    providers.build.return_value = provider
    body = _body()
    body["message"]["parts"].append(
        {
            "raw": "iVBORw0KGgo=",
            "filename": "chart.png",
            "mediaType": "image/png",
        }
    )
    runtime_settings = _settings(
        tmp_path,
        allowed_input_media_types=("text/plain", "image/png"),
    )

    async with asgi_client(_app(client, providers, runtime_settings)) as http:
        response = await http.post(
            f"/api/agents/{AGENT_UID}/responses",
            content=json.dumps(body),
            headers={"Content-Type": "application/a2a+json"},
        )

    assert response.status_code == 200
    providers.validate_input_media.assert_called_once_with(
        "openai",
        "gpt-5.1",
        {"image/png"},
    )
    prompt = instances[0].prompts[0]
    assert prompt.content[0].text == "Answer this."
    assert prompt.content[1].mime_type == "image/png"
    assert prompt.content[1].data == "iVBORw0KGgo="
    assert list(tmp_path.iterdir()) == []


async def test_unscoped_llm_routes_do_not_exist(asgi_client, tmp_path):
    app = _app(AsyncMock(), Mock(), _settings(tmp_path))
    async with asgi_client(app) as http:
        response = await http.post("/api/llm/chat", json={})
        stream = await http.post("/api/llm/chat/stream", json={})

    assert response.status_code == 404
    assert stream.status_code == 404


def test_application_route_table_contains_only_agent_scoped_response_endpoints(
    tmp_path,
):
    app = create_app(_settings(tmp_path))
    paths = set(app.openapi()["paths"])

    assert "/api/agents/{agent_uid}/responses" in paths
    assert "/api/agents/{agent_uid}/responses/stream" in paths
    assert not any(path.startswith("/api/llm") for path in paths)
