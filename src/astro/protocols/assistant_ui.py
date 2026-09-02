"""Assistant UI/Vercel data-stream encoder."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from astro.runtime.events import AstroRuntimeEvent
from astro.runtime.failures import is_terminal_assistant_message, terminal_assistant_failure


@dataclass(slots=True)
class AssistantUiEncoder:
    text_ids: dict[int, str] = field(default_factory=dict)
    reasoning_ids: dict[int, str] = field(default_factory=dict)
    tool_names: dict[str, str] = field(default_factory=dict)
    finished: bool = False
    terminal_error_text: str | None = None

    def encode(self, event: AstroRuntimeEvent) -> list[dict[str, Any]]:
        event_type = event.type
        data = event.data
        if event_type == "lifecycle":
            return [
                {
                    "type": "data-runtime-lifecycle",
                    "data": {"phase": str(data.get("phase") or "")},
                }
            ]
        if event_type == "text_start":
            index = int(data.get("contentIndex", 0))
            text_id = self.text_ids.setdefault(index, f"text-{index}")
            return [{"type": "text-start", "id": text_id}]
        if event_type == "text_delta":
            index = int(data.get("contentIndex", 0))
            text_id = self.text_ids.setdefault(index, f"text-{index}")
            return [
                {
                    "type": "text-delta",
                    "id": text_id,
                    "textDelta": str(data.get("delta", "")),
                }
            ]
        if event_type == "text_end":
            index = int(data.get("contentIndex", 0))
            text_id = self.text_ids.setdefault(index, f"text-{index}")
            return [{"type": "text-end", "id": text_id}]
        if event_type == "thinking_start":
            index = int(data.get("contentIndex", 0))
            reasoning_id = self.reasoning_ids.setdefault(index, f"reasoning-{index}")
            return [{"type": "reasoning-start", "id": reasoning_id}]
        if event_type == "thinking_delta":
            index = int(data.get("contentIndex", 0))
            reasoning_id = self.reasoning_ids.setdefault(index, f"reasoning-{index}")
            return [
                {
                    "type": "reasoning-delta",
                    "id": reasoning_id,
                    "delta": str(data.get("delta", "")),
                }
            ]
        if event_type == "thinking_end":
            index = int(data.get("contentIndex", 0))
            reasoning_id = self.reasoning_ids.setdefault(index, f"reasoning-{index}")
            return [{"type": "reasoning-end", "id": reasoning_id}]
        if event_type == "toolcall_end":
            tool_call = data.get("toolCall", {})
            if not isinstance(tool_call, dict):
                return []
            tool_call_id = str(tool_call.get("id", ""))
            name = str(tool_call.get("name", "unknown"))
            self.tool_names[tool_call_id] = name
            return [
                {
                    "type": "tool-input-available",
                    "toolCallId": tool_call_id,
                    "toolName": name,
                    "input": tool_call.get("arguments", {}),
                }
            ]
        if event_type == "tool_execution_update":
            tool_call_id = str(data.get("toolCallId", ""))
            result = data.get("partialResult", {})
            return [
                {
                    "type": "tool-output-delta",
                    "toolCallId": tool_call_id,
                    "output": result,
                }
            ]
        if event_type == "tool_execution_end":
            tool_call_id = str(data.get("toolCallId", ""))
            return [
                {
                    "type": "tool-output-available",
                    "toolCallId": tool_call_id,
                    "output": data.get("result"),
                    "isError": bool(data.get("isError", False)),
                }
            ]
        if event_type == "message_end":
            failure = terminal_assistant_failure(data)
            if failure is not None:
                self.terminal_error_text = failure.message
            elif is_terminal_assistant_message(data):
                # A later successful terminal message means Tau recovered from
                # an earlier error, for example through overflow compaction.
                self.terminal_error_text = None
            return []
        if event_type == "error":
            if self.terminal_error_text is None:
                self.terminal_error_text = _assistant_error_message(data)
            return []
        if event_type == "agent_settled" and not self.finished:
            if self.terminal_error_text is not None:
                return []
            self.finished = True
            return [{"type": "finish", "finishReason": "stop"}]
        return []

    def finalize(self) -> list[dict[str, Any]]:
        """Emit the one terminal frame after the runtime has finished settling."""
        if self.finished:
            return []
        self.finished = True
        if self.terminal_error_text is not None:
            return [{"type": "error", "errorText": self.terminal_error_text}]
        return [{"type": "finish", "finishReason": "stop"}]

    @staticmethod
    def sse(payload: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()

    @staticmethod
    def done() -> bytes:
        return b"data: [DONE]\n\n"


def _assistant_error_message(data: dict[str, Any]) -> str:
    error = data.get("error", {})
    if isinstance(error, dict):
        return str(error.get("errorMessage") or error.get("error_message") or "Provider error")
    return str(error or "Provider error")
