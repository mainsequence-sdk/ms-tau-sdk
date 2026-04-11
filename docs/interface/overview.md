# Interface Overview

This interface exposes Astro over HTTP using assistant-ui's `ui-message-stream` protocol over SSE.
Clients must identify the backend agent (`agentName`) and user (`userId`) on each request.

## Base URL

Default:

```
http://0.0.0.0:8787
```

The server binds to the LAN address when `ASTRO_STREAM_HOST=0.0.0.0`.

## Endpoints

- `GET /health`
- `GET /api/chat`
- `POST /api/chat`

See the request and response docs for the full contract.
