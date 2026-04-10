# Astro Stream Interface

This interface exposes Astro over HTTP using assistant-ui's `ui-message-stream` protocol over SSE.

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

Send a request compatible with assistant-ui's data-stream runtime:

```json
{
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

The response includes an `X-Thread-Id` header if a new thread was created.

This endpoint expects `messages` to contain the current user turn only. The server reads only the
last message entry and treats it as the exact latest user message, plus optional UI metadata in
`context`.

Conversation continuity comes from `threadId`, which maps to a Pi session file on the server.

The stream wrapper injects:

- `system` as an optional prompt prefix
- `context` as structured UI context
- `tools` as optional UI tool metadata
- only the last `messages` entry as the turn input

Response headers include:

- `Content-Type: text/event-stream`
- `X-Stream-Protocol: ui-message-stream`

The stream ends with a final `data: [DONE]` marker after the `finish` or `error` chunk.

## Session storage

Each `threadId` maps to a session file stored at:

`ASTRO_STREAM_SESSION_DIR/<threadId>.jsonl`

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
