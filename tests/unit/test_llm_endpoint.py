from unittest.mock import ANY, AsyncMock, Mock

from fastapi import FastAPI
from tau_agent.messages import AssistantMessage

import astro.api.llm as llm_api
from astro.api.dependencies import backend, provider_factory, settings
from astro.api.llm import router
from astro.settings import Settings


def _app(client: AsyncMock, providers: Mock) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[backend] = lambda: client
    app.dependency_overrides[provider_factory] = lambda: providers
    app.dependency_overrides[settings] = lambda: Settings(_env_file=None)
    return app


def _fake_harness(monkeypatch, outputs: list[AssistantMessage]) -> list[object]:
    instances: list[object] = []

    class FakeHarness:
        def __init__(self, config, *, messages):
            self.config = config
            self.messages = list(messages)
            self.prompts: list[str] = []
            instances.append(self)

        async def prompt(self, prompt: str):
            self.prompts.append(prompt)
            self.messages.append(outputs.pop(0))
            yield object()

    monkeypatch.setattr(llm_api, "AgentHarness", FakeHarness)
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


async def test_stateless_chat_hydrates_selection_and_closes_provider(
    monkeypatch,
    asgi_client,
):
    instances = _fake_harness(monkeypatch, [_assistant("Final answer")])
    client = AsyncMock()
    client.hydrate_provider_credential.return_value = object()
    provider = Mock()
    provider.aclose = AsyncMock()
    providers = Mock()
    providers.build.return_value = provider

    async with asgi_client(_app(client, providers)) as http:
        response = await http.post(
            "/api/llm/chat",
            json={
                "agent_session_uid": "session-1",
                "model": "gpt-5.1",
                "messages": [
                    {"role": "system", "content": "Be precise."},
                    {"role": "user", "content": "Answer this."},
                ],
                "max_tokens": 256,
                "metadata": {
                    "astro": {
                        "provider": "openai",
                        "timeout_seconds": 30,
                    }
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["message"] == {
        "role": "assistant",
        "content": "Final answer",
    }
    assert response.json()["usage"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    client.hydrate_provider_credential.assert_awaited_once_with(
        "openai",
        session_uid="session-1",
        holder_id=ANY,
    )
    providers.validate_selection.assert_called_once_with("openai", "gpt-5.1")
    providers.build.assert_called_once_with(
        client.hydrate_provider_credential.return_value,
        model="gpt-5.1",
        max_tokens=256,
        timeout_seconds=30.0,
    )
    assert instances[0].prompts == ["Answer this."]
    assert instances[0].config.system == "Be precise."
    provider.aclose.assert_awaited_once()


async def test_stateless_chat_repairs_strict_json(monkeypatch, asgi_client):
    instances = _fake_harness(
        monkeypatch,
        [_assistant("not-json"), _assistant('{"answer":42}')],
    )
    client = AsyncMock()
    provider = Mock()
    provider.aclose = AsyncMock()
    providers = Mock()
    providers.build.return_value = provider

    async with asgi_client(_app(client, providers)) as http:
        response = await http.post(
            "/api/llm/chat",
            json={
                "agent_session_uid": "session-1",
                "model": "gpt-5.1",
                "messages": [{"role": "user", "content": "Return an answer."}],
                "response_format": {"type": "json_object", "strict": True},
                "metadata": {"astro": {"json_repair": {"attempts": 1}}},
            },
        )

    assert response.status_code == 200
    assert response.json()["message"]["content"] == '{"answer":42}'
    assert response.json()["json"] == {"answer": 42}
    assert len(instances[0].prompts) == 2
    assert "Return only valid JSON" in instances[0].config.system
    assert "Repair this assistant response" in instances[0].prompts[1]
    provider.aclose.assert_awaited_once()


async def test_stateless_chat_rejects_unknown_controls_before_hydration(
    monkeypatch,
    asgi_client,
):
    _fake_harness(monkeypatch, [])
    client = AsyncMock()
    providers = Mock()

    async with asgi_client(_app(client, providers)) as http:
        response = await http.post(
            "/api/llm/chat",
            json={
                "agent_session_uid": "session-1",
                "model": "gpt-5.1",
                "messages": [{"role": "user", "content": "Hello"}],
                "metadata": {"astro": {"unrecognized": True}},
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == ("Unsupported metadata.astro controls: unrecognized")
    client.hydrate_provider_credential.assert_not_awaited()
    providers.build.assert_not_called()


async def test_stateless_chat_surfaces_provider_failure_and_closes_provider(
    monkeypatch,
    asgi_client,
):
    _fake_harness(
        monkeypatch,
        [_assistant("", stop_reason="error", error="provider unavailable")],
    )
    client = AsyncMock()
    provider = Mock()
    provider.aclose = AsyncMock()
    providers = Mock()
    providers.build.return_value = provider

    async with asgi_client(_app(client, providers), raise_app_exceptions=False) as http:
        response = await http.post(
            "/api/llm/chat",
            json={
                "agent_session_uid": "session-1",
                "model": "gpt-5.1",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )

    assert response.status_code == 502
    assert provider.aclose.await_count == 1
