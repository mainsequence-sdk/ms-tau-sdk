import pytest

from astro.protocols.assistant_ui import AssistantUiEncoder
from astro.runtime.events import AstroRuntimeEvent


def test_text_and_reasoning_events_use_stable_assistant_ui_ids():
    encoder = AssistantUiEncoder()

    chunks = []
    for event in [
        AstroRuntimeEvent(type="text_start", data={"contentIndex": 0}),
        AstroRuntimeEvent(
            type="text_delta",
            data={"contentIndex": 0, "delta": "hello"},
        ),
        AstroRuntimeEvent(type="text_end", data={"contentIndex": 0}),
        AstroRuntimeEvent(type="agent_settled"),
    ]:
        chunks.extend(encoder.encode(event))

    assert chunks == [
        {"type": "text-start", "id": "text-0"},
        {"type": "text-delta", "id": "text-0", "textDelta": "hello"},
        {"type": "text-end", "id": "text-0"},
        {"type": "finish", "finishReason": "stop"},
    ]


def test_tool_events_preserve_call_identity():
    encoder = AssistantUiEncoder()

    chunks = encoder.encode(
        AstroRuntimeEvent(
            type="toolcall_end",
            data={
                "toolCall": {
                    "id": "call-1",
                    "name": "grep",
                    "arguments": {"pattern": "Tau"},
                }
            },
        )
    )

    assert chunks == [
        {
            "type": "tool-input-available",
            "toolCallId": "call-1",
            "toolName": "grep",
            "input": {"pattern": "Tau"},
        }
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
            AstroRuntimeEvent(
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
    assert encoder.encode(AstroRuntimeEvent(type="agent_settled")) == []
    assert encoder.finalize() == [{"type": "error", "errorText": provider_message}]
    assert encoder.encode(AstroRuntimeEvent(type="agent_settled")) == []


def test_direct_tau_error_uses_the_same_deferred_terminal_contract():
    encoder = AssistantUiEncoder()

    assert (
        encoder.encode(
            AstroRuntimeEvent(
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
        AstroRuntimeEvent(
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
        AstroRuntimeEvent(
            type="message_end",
            data={"message": {"role": "assistant", "stopReason": "stop"}},
        )
    )

    assert encoder.encode(AstroRuntimeEvent(type="agent_settled")) == [
        {"type": "finish", "finishReason": "stop"}
    ]
