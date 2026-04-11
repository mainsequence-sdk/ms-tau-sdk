# Astro Stream Interface

This interface exposes Astro over HTTP using assistant-ui's `ui-message-stream` protocol over SSE.
For the canonical contract, see the docs at `docs/interface/`.

## Run

```bash
npm run pi:stream
```

Defaults:

- host: `0.0.0.0`
- port: `8787`

Environment overrides:

- `ASTRO_STREAM_HOST`
- `ASTRO_STREAM_PORT`
- `ASTRO_STREAM_CORS_ORIGIN` (default `*`)
- `ASTRO_STREAM_SESSION_DIR` (default `<repo>/.astro/stream-sessions`)

## Endpoints

### `POST /api/chat`

Send a request compatible with assistant-ui's `ui-message-stream` runtime:

```json
{
  "newChat": true,
  "agentName": "astro-orchestrator",
  "userId": "user_123",
  "system": "optional system prompt",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "the exact message the user just typed"
        }
      ]
    }
  ],
  "tools": {},
  "threadId": "optional-thread-id",
  "context": {
    "appId": "...",
    "appTitle": "...",
    "currentPath": "...",
    "surfaceId": "...",
    "surfaceTitle": "...",
    "surfaceActions": [],
    "surfaceContextSource": "surface",
    "surfaceDetails": {},
    "surfaceSummary": "...",
    "userId": "..."
  }
}
```

The response is always SSE and uses `event: message` with one JSON chunk per event.

The response includes `X-Thread-Id`. When backend registration succeeds, it also includes:

- `X-Agent-Id`
- `X-Agent-Unique-Id`
- `X-Agent-Session-Id` (when a new session is created)
- `X-Session-Key`

This endpoint expects `messages` to contain the current user turn only. The server reads only the
last message entry and treats it as the exact latest user message, plus optional UI metadata in
`context`.

Conversation continuity comes from the backend agent session key. When `newChat` is `false`,
the client must send `runtime_session_id` to resume the existing session.

The stream wrapper injects:

- `system` as an optional prompt prefix
- `context` as structured UI context
- `tools` as optional UI tool metadata
- only the last `messages` entry as the turn input
- `newChat` as the signal to create a new backend AgentSession

Response headers include:

- `Content-Type: text/event-stream`
- `X-Stream-Protocol: ui-message-stream`
- `X-Agent-Id` when the backend returned an Agent `id` for the thread

The stream ends with a final `data: [DONE]` marker after the `finish` or `error` chunk.

Each stream chunk now has this envelope:

```json
{
  "type": "text-delta",
  "textDelta": "hello",
  "agent_id": 123
}
```

## Session storage

When a new session is created, the stream emits a `new_session` chunk before `start`.

Session files are stored at:

`ASTRO_STREAM_SESSION_DIR/<agent_unique_id>__session_<n>.jsonl`

### `GET /health`

Returns a simple status payload:

```json
{ "ok": true }
```

## Logging

Traffic logging is enabled by default. Disable with:

```bash
ASTRO_STREAM_LOG_TRAFFIC=0
```
