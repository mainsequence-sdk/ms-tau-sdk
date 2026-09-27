"""The local chat history projection matches the platform's Tau history projection."""

from __future__ import annotations

from typing import Any

from ms_tau_sdk.protocols.chat_history import (
    current_branch,
    latest_message_preview,
    project_history,
)
from ms_tau_sdk.runtime.events import TauRuntimeEvent
from ms_tau_sdk.runtime.live_turns import LiveTurn
from ms_tau_sdk.runtime.provenance import PROVENANCE_NAMESPACE

TARGET = "local-agent-workspace"


def _message(entry_id: str, parent_id: str | None, second: int, **message: Any) -> dict[str, Any]:
    return {
        "id": entry_id,
        "parent_id": parent_id,
        "timestamp": 1784851200.0 + second,
        "type": "message",
        "message": {**message, "timestamp": (1784851200 + second) * 1000},
    }


def _stamp(entry_id: str, parent_id: str | None, second: int, **data: Any) -> dict[str, Any]:
    return {
        "id": entry_id,
        "parent_id": parent_id,
        "timestamp": 1784851200.0 + second,
        "type": "custom",
        "namespace": PROVENANCE_NAMESPACE,
        "data": data,
    }


def _project(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return project_history(current_branch(entries), target_agent_uid=TARGET).messages


def test_tool_calls_carry_their_results_and_thinking_becomes_reasoning():
    # The platform's own fixture for this projection, ported unchanged.
    entries = [
        _message("user-entry", None, 0, role="user", content="list my repositories"),
        _message(
            "assistant-call",
            "user-entry",
            1,
            role="assistant",
            content=[
                {"type": "thinking", "thinking": "I should list them."},
                {"type": "text", "text": "Let me check."},
                {
                    "type": "toolCall",
                    "id": "call-1",
                    "name": "mainsequence__code_repository_list",
                    "arguments": {"limit": 5},
                },
            ],
        ),
        _message(
            "tool-result",
            "assistant-call",
            2,
            role="toolResult",
            toolCallId="call-1",
            toolName="mainsequence__code_repository_list",
            content=[{"type": "text", "text": "[]"}],
            details={"mcp_tool": "code_repository_list", "is_error": False},
            isError=False,
        ),
        _message(
            "assistant-answer",
            "tool-result",
            3,
            role="assistant",
            content=[{"type": "text", "text": "You have no repositories."}],
        ),
    ]

    messages = _project(entries)

    assert messages == [
        {
            "id": "u_1",
            "role": "user",
            "createdAt": "2026-07-24T00:00:00Z",
            "completedAt": None,
            "content": [{"type": "text", "text": "list my repositories"}],
            "provenance": None,
        },
        {
            "id": "a_1",
            "role": "assistant",
            "createdAt": "2026-07-24T00:00:01Z",
            "completedAt": "2026-07-24T00:00:01Z",
            "content": [
                {"type": "reasoning", "text": "I should list them."},
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool-call",
                    "toolCallId": "call-1",
                    "toolName": "mainsequence__code_repository_list",
                    "args": {"limit": 5},
                    "isError": False,
                    "result": {
                        "content": [{"type": "text", "text": "[]"}],
                        "details": {"mcp_tool": "code_repository_list", "is_error": False},
                    },
                },
            ],
            "provenance": None,
        },
        {
            "id": "a_2",
            "role": "assistant",
            "createdAt": "2026-07-24T00:00:03Z",
            "completedAt": "2026-07-24T00:00:03Z",
            "content": [{"type": "text", "text": "You have no repositories."}],
            "provenance": None,
        },
    ]


def test_a_failed_tool_call_is_marked_as_an_error():
    entries = [
        _message("u", None, 0, role="user", content="read it"),
        _message(
            "a",
            "u",
            1,
            role="assistant",
            content=[{"type": "toolCall", "id": "c", "name": "read", "arguments": {}}],
        ),
        _message(
            "r",
            "a",
            2,
            role="toolResult",
            toolCallId="c",
            toolName="read",
            content=[{"type": "text", "text": "missing"}],
            isError=True,
        ),
    ]

    [_, assistant] = _project(entries)

    assert assistant["content"] == [
        {
            "type": "tool-call",
            "toolCallId": "c",
            "toolName": "read",
            "args": {},
            "isError": True,
            "result": {"content": [{"type": "text", "text": "missing"}]},
        }
    ]


def test_provenance_stamps_attach_to_the_next_user_message():
    entries = [
        _stamp("stamp-a2a", None, -1, channel="a2a", origin="agent", ignored="x"),
        _message("user-1", "stamp-a2a", 0, role="user", content="hello from another agent"),
        _message("assistant-1", "user-1", 1, role="assistant", content="hi"),
        _stamp("stamp-chat", "assistant-1", 2, channel="chat", origin="user"),
        _message("user-2", "stamp-chat", 3, role="user", content="hello from a person"),
    ]

    messages = _project(entries)

    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assert messages[0]["provenance"] == {
        "channel": "a2a",
        "origin": "agent",
        "targetAgentUid": TARGET,
    }
    assert messages[1]["provenance"] is None
    assert messages[2]["provenance"] == {
        "channel": "chat",
        "origin": "user",
        "targetAgentUid": TARGET,
    }


def test_provenance_keeps_only_verified_actor_fields():
    user_uid = "2b7f1c48-3d1e-4a5b-9c6d-0e1f2a3b4c5d"
    agent_uid = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
    entries = [
        _stamp(
            "agent-stamp",
            None,
            0,
            channel="a2a",
            origin="agent",
            actorKind="AGENT",
            actorUid=agent_uid,
            actorName="forged name",
            unknown="drop me",
        ),
        _message("agent-message", "agent-stamp", 1, role="user", content="agent request"),
        _stamp(
            "user-stamp",
            "agent-message",
            2,
            channel="chat",
            origin="user",
            actorKind="user",
            actorUid=user_uid,
            actorName="u" * 300,
        ),
        _message("user-message", "user-stamp", 3, role="user", content="user request"),
        _stamp(
            "local-stamp",
            "user-message",
            4,
            channel="chat",
            origin="user",
            actorKind="user",
            actorUid="local-mainsequence-user",
        ),
        _message("local-message", "local-stamp", 5, role="user", content="local request"),
    ]

    agent, user, local = [message["provenance"] for message in _project(entries)]

    # Local mode has no Agent registry to resolve a caller Agent's name from.
    assert agent == {
        "channel": "a2a",
        "origin": "agent",
        "actorKind": "agent",
        "actorUid": agent_uid,
        "targetAgentUid": TARGET,
    }
    assert user["actorName"] == "u" * 255
    assert user["actorUid"] == user_uid
    assert local == {
        "channel": "chat",
        "origin": "user",
        "actorKind": "user",
        "targetAgentUid": TARGET,
    }


def test_only_the_active_branch_is_projected():
    entries = [
        _message("root", None, 0, role="user", content="question"),
        _message("abandoned", "root", 1, role="assistant", content="first try"),
        _message("retry", "root", 2, role="assistant", content="second try"),
        _message("follow-up", "retry", 3, role="user", content="thanks"),
    ]

    messages = _project(entries)

    assert [(message["id"], message["content"][0]["text"]) for message in messages] == [
        ("u_1", "question"),
        ("a_1", "second try"),
        ("u_2", "thanks"),
    ]


def test_summaries_become_assistant_messages_without_consuming_assistant_ids():
    entries = [
        _message("u", None, 0, role="user", content="one"),
        _message("a", "u", 1, role="assistant", content="two"),
        {
            "id": "compacted",
            "parent_id": "a",
            "timestamp": 1784851202.0,
            "type": "compaction",
            "summary": "They said one and two.",
        },
        _message("u2", "compacted", 3, role="user", content="three"),
        _message("a2", "u2", 4, role="assistant", content="four"),
    ]

    messages = _project(entries)

    assert [message["id"] for message in messages] == [
        "u_1",
        "a_1",
        "compaction_compacted",
        "u_2",
        "a_2",
    ]
    assert messages[2]["role"] == "assistant"
    assert messages[2]["content"] == [
        {
            "type": "text",
            "text": "Previous conversation was compacted. Summary:\n\nThey said one and two.",
        }
    ]


def test_hidden_and_empty_content_is_left_out():
    entries = [
        _message("u", None, 0, role="user", content=[{"type": "image", "data": "x"}]),
        _message(
            "a",
            "u",
            1,
            role="assistant",
            content=[{"type": "thinking", "thinking": "secret", "redacted": True}],
        ),
        _message(
            "a2",
            "a",
            2,
            role="assistant",
            content=[{"type": "text", "text": "<think>plan</think> Visible answer."}],
        ),
        {"id": "info", "parent_id": "a2", "type": "session_info", "timestamp": 1784851203.0},
        _message("bash", "info", 4, role="bashExecution", command="ls", output="x"),
    ]

    messages = _project(entries)

    assert messages == [
        {
            "id": "a_1",
            "role": "assistant",
            "createdAt": "2026-07-24T00:00:02Z",
            "completedAt": "2026-07-24T00:00:02Z",
            "content": [
                {"type": "reasoning", "text": "plan"},
                {"type": "text", "text": "Visible answer."},
            ],
            "provenance": None,
        }
    ]


def test_the_latest_turn_error_and_timestamp_are_reported():
    failed = project_history(
        current_branch(
            [
                _message("u", None, 0, role="user", content="question"),
                _message(
                    "a",
                    "u",
                    1,
                    role="assistant",
                    content=[],
                    stopReason="error",
                    errorMessage="Provider quota exhausted",
                ),
            ]
        )
    )
    retried = project_history(
        current_branch(
            [
                _message("u", None, 0, role="user", content="question"),
                _message("a", "u", 1, role="assistant", content=[], stopReason="error"),
                _message("u2", "a", 2, role="user", content="again"),
            ]
        )
    )

    assert failed.last_turn_error == "Provider quota exhausted"
    assert [message["id"] for message in failed.messages] == ["u_1"]
    assert failed.last_timestamp is not None
    assert failed.last_timestamp.isoformat() == "2026-07-24T00:00:01+00:00"
    assert retried.last_turn_error is None


def test_latest_message_preview_uses_the_newest_visible_text():
    messages = [
        {"content": [{"type": "text", "text": "  first   answer "}]},
        {"content": [{"type": "tool-call", "toolCallId": "c", "toolName": "read"}]},
    ]
    long_message = [{"content": [{"type": "text", "text": "word " * 100}]}]

    assert latest_message_preview(messages) == "first answer"
    assert latest_message_preview([]) is None
    preview = latest_message_preview(long_message)
    assert preview is not None
    assert len(preview) == 240
    assert preview.endswith("…")


def _event(event_type: str, **data: Any) -> TauRuntimeEvent:
    return TauRuntimeEvent(type=event_type, data=data)


def test_a_live_turn_renders_its_prompt_messages_and_partial_answer():
    live = LiveTurn(
        session_uid="session",
        turn_uid="turn",
        prompt="What changed?",
        provenance={"channel": "chat", "origin": "user"},
        started_at=1784851200.0,
    )

    assert live.entries() == [
        {
            "type": "custom",
            "namespace": PROVENANCE_NAMESPACE,
            "data": {"channel": "chat", "origin": "user"},
            "timestamp": 1784851200.0,
        },
        {
            "type": "message",
            "message": {"role": "user", "content": "What changed?", "timestamp": 1784851200000},
        },
    ]

    stored_prompt = {"role": "user", "content": "What changed? (expanded)", "timestamp": 1}
    live.observe(_event("message_start", message=stored_prompt))
    live.observe(_event("message_end", message=stored_prompt))
    live.observe(_event("message_start", message={"role": "assistant", "content": []}))
    partial = {"role": "assistant", "content": [{"type": "text", "text": "Checking"}]}
    live.observe(_event("text_delta", contentIndex=0, delta="Checking", partial=partial))

    assert [entry["message"] for entry in live.entries()[1:]] == [stored_prompt, partial]

    final = {"role": "assistant", "content": [{"type": "text", "text": "Checking done."}]}
    live.observe(_event("message_end", message=final))
    live.observe(_event("tool_execution_start", toolCallId="c", toolName="read", args={}))

    assert [entry["message"] for entry in live.entries()[1:]] == [stored_prompt, final]


def test_each_message_records_the_branch_entry_it_came_from():
    branch = current_branch(
        [
            _stamp("stamp", None, 0, channel="chat", origin="user"),
            _message("u", "stamp", 1, role="user", content="question"),
            _message("empty", "u", 2, role="assistant", content=[]),
            _message("a", "empty", 3, role="assistant", content="answer"),
        ]
    )

    projected = project_history(branch)

    assert [message["id"] for message in projected.messages] == ["u_1", "a_1"]
    assert projected.source_indexes == [1, 3]
