# Response Contract

Responses are **SSE** with:

- `Content-Type: text/event-stream`
- `X-Stream-Protocol: ui-message-stream`
- `X-Thread-Id`
- `X-Agent-Session-Uid` (backend AgentSession uid for the attached session)
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

`GET /api/chat/session-model` also returns JSON, not SSE. It exposes the model binding stored for
the runtime session.
If the latest user message contains the word `MOCK`, `POST /api/chat` returns a synthetic SSE
response immediately and does not attach to or create a backend session.

## Chunk types

The server emits standard assistant-ui stream chunks:

- `start`
- `reasoning-start` / `reasoning-delta` / `reasoning-end`
- `text-start` / `text-delta` / `text-end`
- `tool-call-start` / `tool-call-delta` / `tool-call-end`
- `tool-result`
- `finish`
- `error`

Every chunk also includes:

```json
{ "agent_uid": "agent_123_uid" }
```

Clients must not depend on Astro emitting a `new_session` chunk. The caller should already know the
session uid from the backend control-plane step that created the session before Astro was called.

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
    "backend_request_url": "http://backend/orm/api/agents/v1/sessions/session_52_uid/checkpoint_lease/acquire/",
    "backend_response_text": "{\"agent_session_uid\":\"session_52_uid\",\"error_code\":\"checkpoint_lease_already_held\",\"error_detail\":\"Lease is already held by another runtime.\",\"checkpoint_version\":4,\"bundle_hash\":\"sha256:...\"}",
    "backend_response_body": {
      "agent_session_uid": "session_52_uid",
      "error_code": "checkpoint_lease_already_held",
      "error_detail": "Lease is already held by another runtime.",
      "checkpoint_version": 4,
      "bundle_hash": "sha256:..."
    },
    "backend_checkpoint_version": 4,
    "backend_bundle_hash": "sha256:..."
  },
  "agent_uid": "agent_123_uid"
}
```
