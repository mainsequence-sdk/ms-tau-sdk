# Response Contract

Responses are **SSE** with:

- `Content-Type: text/event-stream`
- `X-Stream-Protocol: ui-message-stream`
- `X-Thread-Id`
- `X-Agent-Id` (backend Agent id)
- `X-Agent-Unique-Id` (deterministic identity)
- `X-Agent-Session-Id` (backend AgentSession id when created)
- `X-Session-Key` (runtime session key)

Each SSE event uses:

```
event: message
data: { ...chunk... }
```

The stream always ends with:

```
data: [DONE]
```

## Chunk types

The server emits standard assistant-ui stream chunks:

- `new_session` (only on `newChat: true` after backend AgentSession creation)
- `start`
- `reasoning-start` / `reasoning-delta` / `reasoning-end`
- `text-start` / `text-delta` / `text-end`
- `tool-call-start` / `tool-call-delta` / `tool-call-end`
- `tool-result`
- `finish`
- `error`

Every chunk also includes:

```json
{ "agent_id": 123 }
```

The `new_session` chunk payload includes:

```json
{
  "type": "new_session",
  "new_session": {
    "agent_session_id": 456,
    "session_key": "astro-orchestrator_user_123__session_1",
    "agent_unique_id": "astro-orchestrator_user_123",
    "thread_id": "thread-001",
    "agent_id": 123
  },
  "agent_id": 123
}
```
