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
  "threadId": "optional-thread-id",
  "system": "optional system prompt",
  "messages": [
    { "role": "user", "content": [{ "type": "text", "text": "Hello" }] }
  ],
  "tools": []
}
```

The response is always SSE and uses `event: message` with one JSON chunk per event.

The response includes an `X-Thread-Id` header if a new thread was created.

This endpoint is stateless. It expects the full message history in each request (assistant-ui already sends this).

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
