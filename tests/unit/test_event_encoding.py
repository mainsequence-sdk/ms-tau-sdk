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
