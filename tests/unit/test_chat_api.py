from fastapi import FastAPI

from astro.api.chat import router
from astro.api.dependencies import runtime_manager
from astro.errors import BackendConflictError
from astro.runtime.events import AstroRuntimeEvent
from astro.settings import Settings

USER_CALLER_HEADERS = {
    "X-Caller-Kind": "user",
    "X-User-UID": "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d",
    "X-Username": "jose",
}
AGENT_CALLER_HEADERS = {
    "X-Caller-Kind": "agent",
    "X-User-UID": "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d",
    "X-Username": "jose",
    "X-Caller-Agent-UID": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "X-Caller-Coding-Agent-Service-UID": "f0e1d2c3-b4a5-4968-8776-655443322110",
    "X-Caller-Agent-Session-UID": "11111111-2222-4333-8444-555555555555",
}


class _ChatManager:
    settings = Settings(_env_file=None)

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []
        self.provenances: list[object] = []
        self.delivered_sessions: list[str] = []

    async def prompt(self, session_uid: str, prompt: str, *, provenance=None):
        self.prompts.append((session_uid, prompt))
        self.provenances.append(provenance)
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
    async def prompt(self, session_uid: str, prompt: str, *, provenance=None):
        self.prompts.append((session_uid, prompt))
        self.provenances.append(provenance)
        raise BackendConflictError(
            "Backend conflict",
            status_code=409,
            detail={
                "error_code": "model_provider_credential_unavailable",
                "error_detail": (
                    "The selected provider credential is expired, invalid, or cannot be refreshed."
                ),
            },
        )
        yield


class _ProviderErrorManager(_ChatManager):
    async def prompt(self, session_uid: str, prompt: str, *, provenance=None):
        self.prompts.append((session_uid, prompt))
        self.provenances.append(provenance)
        yield AstroRuntimeEvent(
            type="message_end",
            data={
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": "Provider supplied billing message",
                }
            },
        )
        yield AstroRuntimeEvent(type="agent_settled")


def _app(manager: _ChatManager) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[runtime_manager] = lambda: manager
    return app


async def test_chat_streams_real_runtime_events_with_assistant_ui_contract(asgi_client):
    manager = _ChatManager()

    async with asgi_client(_app(manager), headers=USER_CALLER_HEADERS) as http:
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

    async with asgi_client(_app(manager), headers=USER_CALLER_HEADERS) as http:
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

    async with asgi_client(_app(manager), headers=USER_CALLER_HEADERS) as http:
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


async def test_chat_streams_terminal_provider_message_as_error_text(asgi_client):
    manager = _ProviderErrorManager()

    async with asgi_client(_app(manager), headers=USER_CALLER_HEADERS) as http:
        response = await http.post(
            "/api/chat",
            json={
                "sessionUid": "session-1",
                "messages": [{"role": "user", "content": "Answer this."}],
            },
        )

    assert response.status_code == 200
    assert (
        'data: {"type":"error","errorText":"Provider supplied billing message"}\n\n'
        in response.text
    )
    assert '"type":"finish"' not in response.text
    assert "body" not in response.text
    assert response.text.endswith("data: [DONE]\n\n")
    assert manager.delivered_sessions == ["session-1"]


async def test_chat_stamps_each_user_turn_as_human_chat_provenance(asgi_client):
    manager = _ChatManager()
    async with asgi_client(_app(manager), headers=USER_CALLER_HEADERS) as http:
        response = await http.post(
            "/api/chat",
            json={
                "sessionUid": "session-1",
                "messages": [{"role": "user", "content": "Answer this."}],
            },
        )

    assert response.status_code == 200
    assert manager.provenances == [
        {
            "channel": "chat",
            "origin": "user",
            "actorKind": "user",
            "actorUid": USER_CALLER_HEADERS["X-User-UID"],
            "actorName": "jose",
        }
    ]


async def test_chat_stamps_an_agent_caller_and_ignores_identity_in_the_body(asgi_client):
    manager = _ChatManager()
    async with asgi_client(_app(manager), headers=USER_CALLER_HEADERS) as http:
        response = await http.post(
            "/api/chat",
            headers=AGENT_CALLER_HEADERS,
            json={
                "sessionUid": "session-1",
                "messages": [{"role": "user", "content": "Answer this."}],
                "caller_agent_uid": "99999999-9999-4999-8999-999999999999",
            },
        )

    assert response.status_code == 200
    assert manager.provenances == [
        {
            "channel": "chat",
            "origin": "agent",
            "actorKind": "agent",
            "actorUid": AGENT_CALLER_HEADERS["X-Caller-Agent-UID"],
            "callerAgentSessionUid": AGENT_CALLER_HEADERS["X-Caller-Agent-Session-UID"],
        }
    ]


async def test_chat_rejects_a_request_without_caller_identity_before_any_turn(asgi_client):
    manager = _ChatManager()
    async with asgi_client(_app(manager), headers=USER_CALLER_HEADERS) as http:
        response = await http.post(
            "/api/chat",
            headers={"X-Caller-Kind": ""},
            json={
                "sessionUid": "session-1",
                "messages": [{"role": "user", "content": "Answer this."}],
            },
        )

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "detail": "Missing or invalid caller identity headers.",
        "code": "runtime_caller_identity_invalid",
    }
    assert manager.prompts == []


async def test_non_message_routes_answer_without_caller_headers(asgi_client, astro_app):
    async with asgi_client(astro_app) as http:
        response = await http.get("/version", headers={"X-Caller-Kind": ""})

    assert response.status_code != 403
