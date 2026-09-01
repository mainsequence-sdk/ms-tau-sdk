from fastapi import FastAPI

from astro.api.chat import router
from astro.api.dependencies import runtime_manager
from astro.errors import BackendConflictError
from astro.runtime.events import AstroRuntimeEvent
from astro.settings import Settings


class _ChatManager:
    settings = Settings(_env_file=None)

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []
        self.delivered_sessions: list[str] = []

    async def prompt(self, session_uid: str, prompt: str):
        self.prompts.append((session_uid, prompt))
        yield AstroRuntimeEvent(type="text_start", data={"contentIndex": 0})
        yield AstroRuntimeEvent(
            type="text_delta",
            data={"contentIndex": 0, "delta": "Tau answer"},
        )
        yield AstroRuntimeEvent(type="text_end", data={"contentIndex": 0})
        yield AstroRuntimeEvent(type="agent_settled")

    async def cancel(self, _session_uid: str) -> bool:
        return True

    def mark_response_delivered(self, session_uid: str) -> bool:
        self.delivered_sessions.append(session_uid)
        return True


class _ProviderCredentialUnavailableManager(_ChatManager):
    async def prompt(self, session_uid: str, prompt: str):
        self.prompts.append((session_uid, prompt))
        raise BackendConflictError(
            "Backend conflict",
            status_code=409,
            detail={
                "error_code": "model_provider_credential_unavailable",
                "error_detail": (
                    "The selected provider credential is expired, invalid, "
                    "or cannot be refreshed."
                ),
            },
        )
        yield


def _app(manager: _ChatManager) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[runtime_manager] = lambda: manager
    return app


async def test_chat_streams_real_runtime_events_with_assistant_ui_contract(asgi_client):
    manager = _ChatManager()

    async with asgi_client(_app(manager)) as http:
        response = await http.post(
            "/api/chat",
            json={
                "sessionUid": "session-1",
                "messages": [{"role": "user", "content": "Answer this."}],
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert '"type":"text-delta"' in response.text
    assert '"textDelta":"Tau answer"' in response.text
    assert '"type":"finish"' in response.text
    assert response.text.endswith("data: [DONE]\n\n")
    assert manager.prompts == [("session-1", "Answer this.")]
    assert manager.delivered_sessions == ["session-1"]


async def test_chat_rejects_missing_user_prompt_before_runtime_execution(asgi_client):
    manager = _ChatManager()

    async with asgi_client(_app(manager)) as http:
        response = await http.post(
            "/api/chat",
            json={
                "sessionUid": "session-1",
                "messages": [{"role": "assistant", "content": "No user prompt."}],
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Request does not contain a user message"
    assert manager.prompts == []


async def test_chat_streams_provider_credential_conflict_as_assistant_message(asgi_client):
    manager = _ProviderCredentialUnavailableManager()

    async with asgi_client(_app(manager)) as http:
        response = await http.post(
            "/api/chat",
            json={
                "sessionUid": "session-1",
                "messages": [{"role": "user", "content": "Answer this."}],
            },
        )

    assert response.status_code == 200
    assert '"type":"text-start"' in response.text
    assert (
        '"textDelta":"The selected provider credential is expired, invalid, '
        'or cannot be refreshed."' in response.text
    )
    assert '"type":"text-end"' in response.text
    assert '"type":"finish"' in response.text
    assert '"type":"error"' not in response.text
    assert response.text.endswith("data: [DONE]\n\n")
