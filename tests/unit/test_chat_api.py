from fastapi import FastAPI

from astro.api.chat import router
from astro.api.dependencies import runtime_manager
from astro.runtime.events import AstroRuntimeEvent
from astro.settings import Settings


class _ChatManager:
    settings = Settings(_env_file=None)

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

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
