# Response Contract

`POST /api/chat` returns `text/event-stream` using the assistant-ui data-stream
protocol. Astro translates Tau events into text, reasoning, tool, finish, and
error chunks. The response always terminates with:

```text
data: [DONE]
```

Tau implementation objects and raw provider events are never exposed.

Chunk vocabulary, in stream order for a turn:

| Tau event | Chunk | Fields |
| --- | --- | --- |
| `turn_start` / `turn_end` | `start-step` / `finish-step` | `finishReason`: `stop`, `tool-calls`, `length`, `error`, `other` |
| `text_start` / `text_delta` / `text_end` | `text-start` / `text-delta` / `text-end` | `id`, `textDelta` |
| `thinking_start` / `thinking_delta` / `thinking_end` | `reasoning-start` / `reasoning-delta` / `reasoning-end` | `id`, `delta` |
| `toolcall_end` | `tool-call-start`, `tool-call-delta`, `tool-call-end` | `toolCallId`, `toolName`, `argsText` (the arguments as JSON) |
| `tool_execution_end` | `tool-result` | `toolCallId`, `result` (the tool's `content` blocks and `details`), `isError` |
| runtime lifecycle | `data-runtime-lifecycle` | `data.phase` |
| settled / failed | `finish` / `error` | `finishReason` / `errorText` |

`tool_execution_update` has no chunk; the final `tool-result` carries the whole
result. A `tool-result` always follows its `tool-call-start`: a call that
reaches execution without having been announced (a resumed turn) is announced
first. Tools obtained from the Main Sequence MCP gateway are named
`mainsequence__<mcp tool>` and their `result.details.mcp_tool` is the canonical
MCP tool name, which is how a client can show that an MCP tool was used.

`POST /api/agents/{agent_uid}/responses` returns the direct A2A Message branch:

```json
{
  "message": {
    "kind": "message",
    "messageId": "msg-agent-1",
    "role": "ROLE_RESPONDER",
    "parts": [{"text": "Ready."}],
    "metadata": {
      "https://mainsequence.ai/a2a/extensions/agent-inference/v1": {
        "agentUid": "11111111-1111-4111-8111-111111111111",
        "resolved": {
          "provider": "openai",
          "model": "gpt-5.4",
          "thinking": "medium"
        }
      }
    }
  }
}
```

Strict JSON is returned as a `Part.data` object. The stream endpoint buffers the
same result and emits one final A2A Message SSE event.
