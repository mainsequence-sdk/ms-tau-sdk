import pytest

from ms_tau_sdk.protocols.assistant_ui import AssistantUiEncoder
from ms_tau_sdk.runtime.events import TauRuntimeEvent


def test_text_and_reasoning_events_use_stable_assistant_ui_ids():
    encoder = AssistantUiEncoder()

    chunks = []
    for event in [
        TauRuntimeEvent(type="text_start", data={"contentIndex": 0}),
        TauRuntimeEvent(
            type="text_delta",
            data={"contentIndex": 0, "delta": "hello"},
        ),
        TauRuntimeEvent(type="text_end", data={"contentIndex": 0}),
        TauRuntimeEvent(type="agent_settled"),
    ]:
        chunks.extend(encoder.encode(event))

    assert chunks == [
        {"type": "text-start", "id": "text-0"},
        {"type": "text-delta", "id": "text-0", "textDelta": "hello"},
        {"type": "text-end", "id": "text-0"},
        {"type": "finish", "finishReason": "stop"},
    ]


def test_tool_events_use_assistant_stream_names_and_preserve_call_identity():
    encoder = AssistantUiEncoder()

    call_chunks = encoder.encode(
        TauRuntimeEvent(
            type="toolcall_end",
            data={
                "toolCall": {
                    "id": "call-1",
                    "name": "mainsequence__code_repository_list",
                    "arguments": {"limit": 5},
                }
            },
        )
    )
    assert call_chunks == [
        {
            "type": "tool-call-start",
            "toolCallId": "call-1",
            "toolName": "mainsequence__code_repository_list",
        },
        {"type": "tool-call-delta", "toolCallId": "call-1", "argsText": '{"limit":5}'},
        {"type": "tool-call-end", "toolCallId": "call-1"},
    ]

    # The execution start of an announced call adds nothing; partial output
    # has no chunk.
    assert (
        encoder.encode(
            TauRuntimeEvent(
                type="tool_execution_start",
                data={
                    "toolCallId": "call-1",
                    "toolName": "mainsequence__code_repository_list",
                    "args": {"limit": 5},
                },
            )
        )
        == []
    )
    assert (
        encoder.encode(
            TauRuntimeEvent(
                type="tool_execution_update",
                data={"toolCallId": "call-1", "partialResult": {"content": []}},
            )
        )
        == []
    )

    result = {
        "content": [{"type": "text", "text": "[]"}],
        "details": {"mcp_tool": "code_repository_list", "is_error": False},
    }
    assert encoder.encode(
        TauRuntimeEvent(
            type="tool_execution_end",
            data={
                "toolCallId": "call-1",
                "toolName": "mainsequence__code_repository_list",
                "result": result,
                "isError": False,
            },
        )
    ) == [{"type": "tool-result", "toolCallId": "call-1", "result": result, "isError": False}]


def test_tool_result_for_an_unannounced_call_announces_it_first():
    encoder = AssistantUiEncoder()

    chunks = encoder.encode(
        TauRuntimeEvent(
            type="tool_execution_end",
            data={
                "toolCallId": "call-9",
                "toolName": "bash",
                "result": {"content": [{"type": "text", "text": "boom"}]},
                "isError": True,
            },
        )
    )

    assert [chunk["type"] for chunk in chunks] == [
        "tool-call-start",
        "tool-call-delta",
        "tool-call-end",
        "tool-result",
    ]
    assert chunks[0]["toolName"] == "bash"
    assert chunks[-1] == {
        "type": "tool-result",
        "toolCallId": "call-9",
        "result": {"content": [{"type": "text", "text": "boom"}]},
        "isError": True,
    }


def test_execution_start_announces_a_call_missed_by_toolcall_end():
    encoder = AssistantUiEncoder()

    chunks = encoder.encode(
        TauRuntimeEvent(
            type="tool_execution_start",
            data={"toolCallId": "call-2", "toolName": "read", "args": {"path": "a.py"}},
        )
    )

    assert chunks == [
        {"type": "tool-call-start", "toolCallId": "call-2", "toolName": "read"},
        {"type": "tool-call-delta", "toolCallId": "call-2", "argsText": '{"path":"a.py"}'},
        {"type": "tool-call-end", "toolCallId": "call-2"},
    ]
    # Announced once: a later toolcall_end for the same id adds nothing.
    assert (
        encoder.encode(
            TauRuntimeEvent(
                type="toolcall_end",
                data={"toolCall": {"id": "call-2", "name": "read", "arguments": {}}},
            )
        )
        == []
    )


@pytest.mark.parametrize(
    ("stop_reason", "finish_reason"),
    [
        ("stop", "stop"),
        ("toolUse", "tool-calls"),
        ("length", "length"),
        ("error", "error"),
        ("aborted", "other"),
        (None, "stop"),
    ],
)
def test_turn_boundaries_become_step_markers(stop_reason, finish_reason):
    encoder = AssistantUiEncoder()

    assert encoder.encode(TauRuntimeEvent(type="turn_start")) == [{"type": "start-step"}]
    message = {"role": "assistant"}
    if stop_reason is not None:
        message["stopReason"] = stop_reason
    assert encoder.encode(TauRuntimeEvent(type="turn_end", data={"message": message})) == [
        {"type": "finish-step", "finishReason": finish_reason}
    ]


@pytest.mark.parametrize("status_code", [401, 402, 429, 503])
def test_terminal_provider_error_is_preserved_without_success_finish(status_code: int):
    encoder = AssistantUiEncoder()
    provider_message = (
        f"provider-under-test request failed with status {status_code} for model example/model: "
        "Provider supplied billing message"
    )

    assert (
        encoder.encode(
            TauRuntimeEvent(
                type="message_end",
                data={
                    "message": {
                        "role": "assistant",
                        "stopReason": "error",
                        "errorMessage": provider_message,
                        "diagnostics": [
                            {
                                "type": "provider_error",
                                "details": {
                                    "status_code": status_code,
                                    "body": "must not be emitted",
                                },
                            }
                        ],
                    }
                },
            )
        )
        == []
    )
    assert encoder.encode(TauRuntimeEvent(type="agent_settled")) == []
    assert encoder.finalize() == [
        {
            "type": "error",
            "errorText": provider_message,
            "status": status_code,
            "error_code": "ProviderError",
        }
    ]
    assert encoder.encode(TauRuntimeEvent(type="agent_settled")) == []


@pytest.mark.parametrize("status_code", [401, 402, 429, 503])
def test_terminal_provider_error_without_a_message_composes_the_status_code(status_code: int):
    encoder = AssistantUiEncoder()

    assert (
        encoder.encode(
            TauRuntimeEvent(
                type="message_end",
                data={
                    "message": {
                        "role": "assistant",
                        "stopReason": "error",
                        "diagnostics": [
                            {
                                "type": "provider_error",
                                "details": {
                                    "status_code": status_code,
                                    "body": "must not be emitted",
                                },
                            }
                        ],
                    }
                },
            )
        )
        == []
    )
    assert encoder.encode(TauRuntimeEvent(type="agent_settled")) == []
    assert encoder.finalize() == [
        {
            "type": "error",
            "errorText": f"Provider error (HTTP {status_code})",
            "status": status_code,
            "error_code": "ProviderError",
        }
    ]


def test_terminal_provider_error_without_diagnostics_keeps_the_bare_fallback():
    encoder = AssistantUiEncoder()

    encoder.encode(
        TauRuntimeEvent(
            type="message_end",
            data={"message": {"role": "assistant", "stopReason": "error"}},
        )
    )

    assert encoder.finalize() == [
        {
            "type": "error",
            "errorText": "Provider error",
            "error_code": "AssistantError",
        }
    ]


def test_direct_tau_error_uses_the_same_deferred_terminal_contract():
    encoder = AssistantUiEncoder()

    assert (
        encoder.encode(
            TauRuntimeEvent(
                type="error",
                data={"error": {"errorMessage": "Provider supplied message"}},
            )
        )
        == []
    )
    assert encoder.finalize() == [{"type": "error", "errorText": "Provider supplied message"}]


def test_successful_retry_replaces_an_earlier_terminal_error():
    encoder = AssistantUiEncoder()

    encoder.encode(
        TauRuntimeEvent(
            type="message_end",
            data={
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": "Context overflow",
                }
            },
        )
    )
    encoder.encode(
        TauRuntimeEvent(
            type="message_end",
            data={"message": {"role": "assistant", "stopReason": "stop"}},
        )
    )

    assert encoder.encode(TauRuntimeEvent(type="agent_settled")) == [
        {"type": "finish", "finishReason": "stop"}
    ]
