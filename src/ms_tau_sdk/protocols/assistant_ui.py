"""Assistant UI/Vercel data-stream encoder.

Chunk vocabulary (what a ``ui-message-stream`` consumer receives):

- ``start-step`` / ``finish-step`` at Tau ``turn_start`` / ``turn_end``;
- ``text-start`` / ``text-delta`` (``textDelta``) / ``text-end``;
- ``reasoning-start`` / ``reasoning-delta`` (``delta``) / ``reasoning-end``;
- ``tool-call-start`` / ``tool-call-delta`` (``argsText``) / ``tool-call-end``
  when the model has finished a tool call, then ``tool-result`` (``result``,
  ``isError``) when the tool has run;
- ``data-runtime-lifecycle``, ``error`` and ``finish``.

The tool chunk names are the ones assistant-stream's ``UIMessageStreamDecoder``
understands; the earlier ``tool-input-available`` / ``tool-output-available``
names were ignored by every consumer. A ``tool-result`` is only valid after its
``tool-call-start``, so the encoder announces a call it has not seen before
emitting the result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ms_tau_sdk.runtime.events import TauRuntimeEvent
from ms_tau_sdk.runtime.failures import is_terminal_assistant_message, terminal_assistant_failure


@dataclass(slots=True)
class AssistantUiEncoder:
    text_ids: dict[int, str] = field(default_factory=dict)
    reasoning_ids: dict[int, str] = field(default_factory=dict)
    tool_names: dict[str, str] = field(default_factory=dict)
    finished: bool = False
    terminal_error_text: str | None = None

    def _tool_call_chunks(
        self,
        *,
        tool_call_id: str,
        name: str,
        arguments: Any,
    ) -> list[dict[str, Any]]:
        self.tool_names[tool_call_id] = name
        args = arguments if isinstance(arguments, dict) else {}
        return [
            {"type": "tool-call-start", "toolCallId": tool_call_id, "toolName": name},
            {
                "type": "tool-call-delta",
                "toolCallId": tool_call_id,
                "argsText": json.dumps(args, separators=(",", ":"), default=str),
            },
            {"type": "tool-call-end", "toolCallId": tool_call_id},
        ]

    def encode(self, event: TauRuntimeEvent) -> list[dict[str, Any]]:
        event_type = event.type
        data = event.data
        if event_type == "turn_start":
            return [{"type": "start-step"}]
        if event_type == "turn_end":
            return [
                {
                    "type": "finish-step",
                    "finishReason": _step_finish_reason(data),
                }
            ]
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
            if not tool_call_id or tool_call_id in self.tool_names:
                return []
            return self._tool_call_chunks(
                tool_call_id=tool_call_id,
                name=str(tool_call.get("name") or "unknown"),
                arguments=tool_call.get("arguments", {}),
            )
        if event_type == "tool_execution_start":
            # Normally announced already by ``toolcall_end``; a call that
            # reaches execution without it (a resumed turn) is announced here.
            tool_call_id = str(data.get("toolCallId", ""))
            if not tool_call_id or tool_call_id in self.tool_names:
                return []
            return self._tool_call_chunks(
                tool_call_id=tool_call_id,
                name=str(data.get("toolName") or "unknown"),
                arguments=data.get("args", {}),
            )
        if event_type == "tool_execution_update":
            # Partial tool output has no assistant-stream chunk; the final
            # ``tool-result`` carries the complete result.
            return []
        if event_type == "tool_execution_end":
            tool_call_id = str(data.get("toolCallId", ""))
            if not tool_call_id:
                return []
            chunks: list[dict[str, Any]] = []
            if tool_call_id not in self.tool_names:
                chunks.extend(
                    self._tool_call_chunks(
                        tool_call_id=tool_call_id,
                        name=str(data.get("toolName") or "unknown"),
                        arguments={},
                    )
                )
            chunks.append(
                {
                    "type": "tool-result",
                    "toolCallId": tool_call_id,
                    "result": data.get("result"),
                    "isError": bool(data.get("isError", False)),
                }
            )
            return chunks
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


_STEP_FINISH_REASONS = {
    "stop": "stop",
    "toolUse": "tool-calls",
    "length": "length",
    "error": "error",
    "aborted": "other",
}


def _step_finish_reason(data: dict[str, Any]) -> str:
    message = data.get("message", {})
    stop_reason = message.get("stopReason") if isinstance(message, dict) else None
    return _STEP_FINISH_REASONS.get(str(stop_reason or ""), "stop")


def _assistant_error_message(data: dict[str, Any]) -> str:
    error = data.get("error", {})
    if isinstance(error, dict):
        return str(error.get("errorMessage") or error.get("error_message") or "Provider error")
    return str(error or "Provider error")
