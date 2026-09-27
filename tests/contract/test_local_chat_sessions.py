"""Local `/api/chat` sessions are recorded, listed, and read back after reload (ADR 0018)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
from fastapi import FastAPI
from tau_agent.messages import AssistantMessage, TextContent, ThinkingContent, ToolCall
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallEndEvent,
)

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

MODEL = "gpt-5.4"
NOTES = "hello from the notes"


class Gate:
    """A point where a scripted stream holds until the test opens it."""

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.opened = asyncio.Event()


class ScriptedProvider:
    """Replays one scripted stream per model request; a stream may hold at a gate."""

    def __init__(self) -> None:
        self._streams: list[list[object]] = []
        self.request_count = 0

    def script(self, *streams: Iterable[object]) -> None:
        self._streams.extend(list(stream) for stream in streams)

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[Any],
        tools: list[Any],
        signal: Any = None,
        session_id: str | None = None,
    ) -> AsyncIterator[object]:
        del model, system, messages, tools, session_id
        self.request_count += 1
        stream = self._streams.pop(0)

        async def iterator() -> AsyncIterator[object]:
            for item in stream:
                if isinstance(item, Gate):
                    # Every earlier event has reached the runtime's consumer by now:
                    # the model stream is pulled one event at a time.
                    item.reached.set()
                    while not item.opened.is_set():
                        if signal is not None and signal.is_cancelled():
                            yield AssistantErrorEvent(
                                reason="aborted",
                                error=AssistantMessage(model=MODEL, stop_reason="aborted"),
                            )
                            return
                        await asyncio.sleep(0.005)
                    continue
                yield item

        return iterator()


def tool_step(call_id: str, path: str, *, thinking: str, text: str) -> list[object]:
    thought = ThinkingContent(thinking=thinking)
    said = TextContent(text=text)
    call = ToolCall(id=call_id, name="read", arguments={"path": path})
    message = AssistantMessage(model=MODEL, stop_reason="toolUse", content=[thought, said, call])
    return [
        ThinkingDeltaEvent(
            content_index=0,
            delta=thinking,
            partial=AssistantMessage(model=MODEL, content=[thought]),
        ),
        TextDeltaEvent(
            content_index=1,
            delta=text,
            partial=AssistantMessage(model=MODEL, content=[thought, said]),
        ),
        ToolCallEndEvent(content_index=2, tool_call=call, partial=message),
        AssistantDoneEvent(reason="toolUse", message=message),
    ]


def answer_step(
    text: str,
    *,
    thinking: str | None = None,
    gate: Gate | None = None,
) -> list[object]:
    content: list[Any] = [ThinkingContent(thinking=thinking)] if thinking else []
    content.append(TextContent(text=text))
    message = AssistantMessage(model=MODEL, stop_reason="stop", content=content)
    events: list[object] = [
        TextDeltaEvent(content_index=len(content) - 1, delta=text, partial=message),
    ]
    if gate is not None:
        events.append(gate)
    events.append(AssistantDoneEvent(reason="stop", message=message))
    return events


def _settings(workspace: Path, state: Path, *, credential: str = "developer") -> TauSDKSettings:
    return TauSDKSettings(
        _env_file=None,
        workspace=workspace,
        local_state_root=state / "local",
        state_root=state / "tau",
        backend_url="http://backend.test",
        auth_mode="jwt",
        local_mode=True,
        access_token=f"{credential}-access-token",
        refresh_token=f"{credential}-refresh-token",
        local_provider="openai",
        local_model=MODEL,
        exclude_mainsequence_mcp=True,
        startup_dependencies_enabled=False,
    )


def _local_app(settings: TauSDKSettings, provider: ScriptedProvider) -> FastAPI:
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
                "model": MODEL,
                "api": "openai-responses",
                "input": ["text"],
                "reasoning": True,
                "thinking_levels": [],
            },
        ),
    )
    remote = Mock(spec=MainSequenceClient)
    remote.auth = Mock()
    remote.hydrate_local_provider_credential = AsyncMock(return_value=evidence)
    remote.aclose = AsyncMock()
    backend = LocalDevelopmentBackend(settings, remote)
    providers = Mock(spec=ProviderFactory)
    providers.for_session_credential.return_value = ProviderRuntime(
        name="openai",
        model=MODEL,
        thinking_level="off",
        provider=provider,
        credential=evidence.credential,
        provider_control=evidence.provider_control,
    )
    services = ApplicationServices(
        settings=settings,
        auth=remote.auth,
        backend=backend,
        providers=providers,
        runtime=SessionRuntimeManager(settings=settings, backend=backend, providers=providers),
    )
    return create_app(settings, services_factory=lambda _settings: services)


@asynccontextmanager
async def _running(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as http,
    ):
        yield http


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text(NOTES + "\n", encoding="utf-8")
    return workspace


async def _chat(http: httpx.AsyncClient, session_uid: str, message: str) -> str:
    response = await http.post("/api/chat", json={"sessionUid": session_uid, "message": message})
    assert response.status_code == 200, response.text
    assert '"type":"error"' not in response.text
    assert response.text.endswith("data: [DONE]\n\n")
    return response.headers["x-agent-session-uid"]


async def _history(http: httpx.AsyncClient, session_uid: str) -> dict[str, Any]:
    response = await http.get(f"/api/local/v1/chat-sessions/{session_uid}/history")
    assert response.status_code == 200, response.text
    return response.json()


def _texts(message: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (part["type"], part.get("text") or part.get("toolName") or "")
        for part in message["content"]
    ]


async def test_new_and_continued_chat_sessions_record_text_reasoning_and_tool_calls(tmp_path):
    settings = _settings(_workspace(tmp_path), tmp_path / "state")
    provider = ScriptedProvider()
    provider.script(
        tool_step("call-1", "notes.txt", thinking="Check the notes file.", text="Reading."),
        answer_step("The notes say hello."),
        tool_step("call-2", "missing.txt", thinking="Look for more.", text="Checking."),
        answer_step("There is nothing else.", thinking="The file is missing."),
    )

    async with _running(_local_app(settings, provider)) as http:
        session_uid = await _chat(http, "crm-assistant", "What do the notes say?")
        first_listing = (await http.get("/api/local/v1/chat-sessions")).json()
        first_history = await _history(http, session_uid)
        continued_uid = await _chat(http, session_uid, "Anything else?")
        listing = (await http.get("/api/local/v1/chat-sessions")).json()
        history = await _history(http, session_uid)

    assert session_uid == settings.local_session_uid("crm-assistant")
    assert continued_uid == session_uid
    assert first_listing["nextCursor"] is None
    [first_summary] = first_listing["sessions"]
    assert first_summary["sessionUid"] == session_uid
    assert first_summary["title"] == "What do the notes say?"
    assert first_summary["messageCount"] == 3
    assert first_summary["latestMessagePreview"] == "The notes say hello."
    assert first_summary["working"] is False
    assert [message["id"] for message in first_history["messages"]] == ["u_1", "a_1", "a_2"]

    assert history["version"] == 1
    assert history["inProgressMessage"] is None
    assert history["session"]["sessionId"] == session_uid
    assert history["session"]["agentSessionUid"] == session_uid
    assert history["session"]["agentUid"] == settings.local_agent_uid
    assert history["session"]["status"] == "completed"
    assert history["session"]["error"] is None
    assert history["session"]["startedAt"] == first_summary["createdAt"]
    messages = history["messages"]
    assert [(message["id"], message["role"]) for message in messages] == [
        ("u_1", "user"),
        ("a_1", "assistant"),
        ("a_2", "assistant"),
        ("u_2", "user"),
        ("a_3", "assistant"),
        ("a_4", "assistant"),
    ]
    assert messages[:3] == first_history["messages"]
    assert messages[0]["content"] == [{"type": "text", "text": "What do the notes say?"}]
    assert messages[0]["completedAt"] is None
    assert messages[0]["provenance"] == {
        "origin": "user",
        "channel": "chat",
        "actorKind": "user",
        "targetAgentUid": settings.local_agent_uid,
    }
    assert messages[1]["provenance"] is None
    assert messages[1]["completedAt"] == messages[1]["createdAt"]
    assert _texts(messages[1]) == [
        ("reasoning", "Check the notes file."),
        ("text", "Reading."),
        ("tool-call", "read"),
    ]
    read_call = messages[1]["content"][2]
    assert read_call["toolCallId"] == "call-1"
    assert read_call["args"] == {"path": "notes.txt"}
    assert read_call["isError"] is False
    assert NOTES in read_call["result"]["content"][0]["text"]
    assert _texts(messages[2]) == [("text", "The notes say hello.")]
    assert messages[3]["content"] == [{"type": "text", "text": "Anything else?"}]
    missing_call = messages[4]["content"][2]
    assert missing_call["toolCallId"] == "call-2"
    assert missing_call["isError"] is True
    assert missing_call["result"]["content"][0]["type"] == "text"
    assert _texts(messages[5]) == [
        ("reasoning", "The file is missing."),
        ("text", "There is nothing else."),
    ]

    [summary] = listing["sessions"]
    assert summary["sessionUid"] == session_uid
    assert summary["title"] == "What do the notes say?"
    assert summary["messageCount"] == 6
    assert summary["latestMessagePreview"] == "There is nothing else."
    assert summary["createdAt"] == first_summary["createdAt"]
    assert summary["updatedAt"] >= first_summary["updatedAt"]
    assert history["session"]["updatedAt"] == summary["updatedAt"]


async def test_chat_sessions_list_newest_first_with_cursor_pagination(tmp_path):
    settings = _settings(_workspace(tmp_path), tmp_path / "state")
    provider = ScriptedProvider()
    provider.script(*(answer_step(f"Answer {index}.") for index in range(3)))

    async with _running(_local_app(settings, provider)) as http:
        created = [await _chat(http, f"session-{index}", f"Question {index}") for index in range(3)]
        first_page = (await http.get("/api/local/v1/chat-sessions", params={"limit": 2})).json()
        second_page = (
            await http.get(
                "/api/local/v1/chat-sessions",
                params={"limit": 2, "cursor": first_page["nextCursor"]},
            )
        ).json()
        invalid = await http.get("/api/local/v1/chat-sessions", params={"cursor": "not-a-cursor"})
        too_large = await http.get("/api/local/v1/chat-sessions", params={"limit": 101})

    listed = [item["sessionUid"] for item in [*first_page["sessions"], *second_page["sessions"]]]
    assert listed == list(reversed(created))
    assert len(first_page["sessions"]) == 2
    assert first_page["nextCursor"]
    assert second_page["nextCursor"] is None
    assert [item["title"] for item in second_page["sessions"]] == ["Question 0"]
    assert invalid.status_code == 400
    assert too_large.status_code == 422


async def test_chat_sessions_and_history_survive_a_tau_restart(tmp_path):
    workspace = _workspace(tmp_path)
    settings = _settings(workspace, tmp_path / "state")
    provider = ScriptedProvider()
    provider.script(
        tool_step("call-1", "notes.txt", thinking="Check the notes file.", text="Reading."),
        answer_step("The notes say hello."),
    )
    async with _running(_local_app(settings, provider)) as http:
        session_uid = await _chat(http, "restart-session", "What do the notes say?")
        before_listing = (await http.get("/api/local/v1/chat-sessions")).json()
        before_history = await _history(http, session_uid)

    restarted_provider = ScriptedProvider()
    restarted_provider.script(answer_step("Still here."))
    async with _running(_local_app(settings, restarted_provider)) as http:
        after_listing = (await http.get("/api/local/v1/chat-sessions")).json()
        after_history = await _history(http, session_uid)
        await _chat(http, session_uid, "Are you still there?")
        continued = await _history(http, session_uid)

    assert after_listing == before_listing
    assert after_history == before_history
    assert [message["id"] for message in continued["messages"]] == [
        "u_1",
        "a_1",
        "a_2",
        "u_2",
        "a_3",
    ]
    assert continued["messages"][:3] == before_history["messages"]
    assert _texts(continued["messages"][4]) == [("text", "Still here.")]


async def test_reload_during_a_running_turn_returns_it_in_progress(tmp_path):
    settings = _settings(_workspace(tmp_path), tmp_path / "state")
    provider = ScriptedProvider()
    gate = Gate()
    provider.script(
        answer_step("First answer."),
        answer_step("Second answer, streaming.", thinking="Think first.", gate=gate),
    )

    async with _running(_local_app(settings, provider)) as http:
        session_uid = await _chat(http, "long-turn", "First question")
        running = asyncio.create_task(
            http.post("/api/chat", json={"sessionUid": session_uid, "message": "Second question"})
        )
        await gate.reached.wait()
        history = await _history(http, session_uid)
        listing = (await http.get("/api/local/v1/chat-sessions")).json()
        busy = await http.post("/api/chat", json={"sessionUid": session_uid, "message": "Third"})
        gate.opened.set()
        response = await running
        completed = await _history(http, session_uid)
        settled_listing = (await http.get("/api/local/v1/chat-sessions")).json()

    assert history["session"]["status"] == "running"
    assert [message["id"] for message in history["messages"]] == ["u_1", "a_1", "u_2"]
    assert history["messages"][2]["content"] == [{"type": "text", "text": "Second question"}]
    assert history["messages"][2]["provenance"]["channel"] == "chat"
    in_progress = history["inProgressMessage"]
    assert in_progress["id"] == "a_2"
    assert in_progress["role"] == "assistant"
    assert in_progress["completedAt"] is None
    assert _texts(in_progress) == [
        ("reasoning", "Think first."),
        ("text", "Second answer, streaming."),
    ]
    [summary] = listing["sessions"]
    assert summary["working"] is True
    assert summary["messageCount"] == 4
    assert summary["latestMessagePreview"] == "Second answer, streaming."
    assert busy.status_code == 409
    assert busy.json()["error"] == "session_busy"

    assert response.status_code == 200
    assert '"textDelta":"Second answer, streaming."' in response.text
    assert completed["session"]["status"] == "completed"
    assert completed["inProgressMessage"] is None
    assert [message["id"] for message in completed["messages"]] == ["u_1", "a_1", "u_2", "a_2"]
    assert completed["messages"][3]["content"] == in_progress["content"]
    assert settled_listing["sessions"][0]["working"] is False
    assert settled_listing["sessions"][0]["messageCount"] == 4
    assert provider.request_count == 2


async def _post_and_disconnect(app: FastAPI, body: dict[str, Any]) -> tuple[int, list[bytes]]:
    """POST to the ASGI app the way uvicorn does and disconnect after the first chunk."""

    disconnected = asyncio.Event()
    request_sent = False
    status: list[int] = []
    chunks: list[bytes] = []

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            status.append(message["status"])
        elif message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            disconnected.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"test"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    return status[0], chunks


async def test_a_client_disconnect_leaves_the_local_turn_running_to_completion(tmp_path):
    settings = _settings(_workspace(tmp_path), tmp_path / "state")
    provider = ScriptedProvider()
    gate = Gate()
    provider.script(answer_step("Finished without its client.", gate=gate))
    app = _local_app(settings, provider)

    async with _running(app) as http:
        status, chunks = await _post_and_disconnect(
            app,
            {"sessionUid": "closed-tab", "message": "Keep going after I leave"},
        )
        session_uid = settings.local_session_uid("closed-tab")
        await gate.reached.wait()
        during = await _history(http, session_uid)
        manager: SessionRuntimeManager = app.state.runtime_manager
        turn = manager._local_chat_turns[session_uid]
        gate.opened.set()
        await asyncio.wait_for(turn, timeout=5)
        history = await _history(http, session_uid)

    assert status == 200
    assert chunks
    assert during["session"]["status"] == "running"
    assert [message["id"] for message in during["messages"]] == ["u_1"]
    assert [message["id"] for message in history["messages"]] == ["u_1", "a_1"]
    assert _texts(history["messages"][1]) == [("text", "Finished without its client.")]


async def test_stop_ends_a_running_local_turn_and_the_session_continues(tmp_path):
    settings = _settings(_workspace(tmp_path), tmp_path / "state")
    provider = ScriptedProvider()
    gate = Gate()
    provider.script(
        answer_step("Never finished.", gate=gate),
        answer_step("Next turn works."),
    )

    async with _running(_local_app(settings, provider)) as http:
        session_uid = settings.local_session_uid("stoppable")
        running = asyncio.create_task(
            http.post("/api/chat", json={"sessionUid": "stoppable", "message": "Start"})
        )
        await gate.reached.wait()
        cancel = await http.post("/api/chat/session/cancel", json={"sessionUid": session_uid})
        stopped = await running
        stopped_history = await _history(http, session_uid)
        await _chat(http, session_uid, "Try again")
        history = await _history(http, session_uid)

    assert cancel.status_code == 200
    assert stopped.status_code == 200
    assert stopped_history["session"]["status"] == "completed"
    assert stopped_history["inProgressMessage"] is None
    assert [message["id"] for message in history["messages"]][-2:] == ["u_2", "a_1"]
    assert _texts(history["messages"][-1]) == [("text", "Next turn works.")]


async def test_chat_sessions_are_scoped_to_the_authenticated_local_user(tmp_path):
    workspace = _workspace(tmp_path)
    owner = _settings(workspace, tmp_path / "state", credential="owner")
    provider = ScriptedProvider()
    provider.script(answer_step("Private answer."))
    async with _running(_local_app(owner, provider)) as http:
        session_uid = await _chat(http, "private", "Private question")

    other = _settings(workspace, tmp_path / "state", credential="someone-else")
    async with _running(_local_app(other, ScriptedProvider())) as http:
        listing = (await http.get("/api/local/v1/chat-sessions")).json()
        history = await http.get(f"/api/local/v1/chat-sessions/{session_uid}/history")
        continued = await http.post(
            "/api/chat",
            json={"sessionUid": session_uid, "message": "Let me in"},
        )

    assert listing == {"sessions": [], "nextCursor": None}
    assert history.status_code == 404
    assert history.json()["error"] == "session_not_found"
    assert continued.status_code == 404
    assert "Private" not in history.text + continued.text


async def test_local_agent_identity_comes_from_the_workspace_agent_card(tmp_path):
    workspace = _workspace(tmp_path)
    settings = _settings(workspace, tmp_path / "state")
    async with _running(_local_app(settings, ScriptedProvider())) as http:
        without_card = (await http.get("/api/local/v1/agent")).json()
        (workspace / ".agents").mkdir()
        (workspace / ".agents" / "agent_card.json").write_text(
            json.dumps(
                {
                    "name": "  CRM assistant ",
                    "description": "Answers questions about the CRM.",
                    "version": "1.0.0",
                }
            ),
            encoding="utf-8",
        )
        with_card = (await http.get("/api/local/v1/agent")).json()

    assert without_card == {"name": None, "displayName": None, "description": None}
    assert with_card == {
        "name": "CRM assistant",
        "displayName": "CRM assistant",
        "description": "Answers questions about the CRM.",
    }


async def test_local_chat_routes_are_rejected_outside_local_mode(sdk_client):
    for path in (
        "/api/local/v1/chat-sessions",
        "/api/local/v1/chat-sessions/any-session/history",
        "/api/local/v1/agent",
    ):
        response = await sdk_client.get(path)
        assert response.status_code == 409
        assert response.json()["detail"] == "Local chat sessions require TAU_LOCAL_MODE=true"
