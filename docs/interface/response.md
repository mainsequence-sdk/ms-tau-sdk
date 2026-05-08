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

`GET /api/chat/history` returns compact JSON history, not SSE. See
[`history-hydration.md`](./history-hydration.md) for the response shape.
`GET /api/chat/session-model` also returns JSON, not SSE. It exposes the model binding stored for
the runtime session.
If the latest user message contains the word `MOCK`, `POST /api/chat` returns a synthetic SSE
response immediately and does not create or resume a backend session.

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
    "session_key": "456",
    "runtime_session_id": "456",
    "agent_name": "astro-orchestrator",
    "agent_unique_id": "astro-orchestrator_user_123",
    "thread_id": "thread-001",
    "agent_id": 123
  },
  "agent_id": 123
}
```

The `error` chunk keeps `error` as the human-readable field and includes `error_source` so the
frontend can distinguish backend, provider, Pi, client, checkpoint, tool, and Astro
failures. Astro prefixes the human-readable `error` string with `[<error_source>]` before streaming
it. When a backend call returns `error_code`, `error_detail`, or `field_errors`, Astro passes those
values through to the SSE chunk instead of replacing them with a generic message. Backend-origin
stream errors also include `forensics` so the frontend can expose or log the backend
request/response context that caused the stream failure.

```json
{
  "type": "error",
  "error": "[checkpoint] Checkpoint lease failed: Lease is already held by another runtime.",
  "error_source": "checkpoint",
  "status": 409,
  "error_code": "checkpoint_lease_already_held",
  "error_detail": "Lease is already held by another runtime.",
  "field_errors": null,
  "forensics": {
    "backend_request_url": "http://backend/orm/api/agents/v1/sessions/52/checkpoint_lease/acquire/",
    "backend_response_text": "{\"agent_session_id\":52,\"error_code\":\"checkpoint_lease_already_held\",\"error_detail\":\"Lease is already held by another runtime.\",\"checkpoint_version\":4,\"bundle_hash\":\"sha256:...\"}",
    "backend_response_body": {
      "agent_session_id": 52,
      "error_code": "checkpoint_lease_already_held",
      "error_detail": "Lease is already held by another runtime.",
      "checkpoint_version": 4,
      "bundle_hash": "sha256:..."
    },
    "backend_checkpoint_version": 4,
    "backend_bundle_hash": "sha256:..."
  },
  "agent_id": 123
}
```
