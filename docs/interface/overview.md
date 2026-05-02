# Interface Overview

This interface exposes Astro over HTTP using assistant-ui's `ui-message-stream` protocol over SSE.
Clients must identify the backend agent (`agentName`) and user (`userId`) on each request.
The normal entry point is `astro-orchestrator`; project-scoped follow-up sessions can target
`mainsequence-project-coder` directly once the project id and checked-out path are known. Image-backed
remote worker pods can pin the runtime to `mainsequence-project-executor` with
`ASTRO_FIXED_AGENT_NAME` plus fixed project env vars when the project image already contains the
prepared runtime.

## Base URL

Default:

```
http://0.0.0.0:8787
```

The server binds to the LAN address when `ASTRO_STREAM_HOST=0.0.0.0`.

## Endpoints

- `GET /health`
- `GET /api/chat`
- `GET /api/chat/get_available_models`
- `GET /api/models/catalog`
- `GET /api/model-providers`
- `GET /api/model-providers/:provider/signin/:attemptId`
- `GET /api/chat/diff`
- `GET /api/chat/history`
- `GET /api/chat/session-model`
- `GET /api/chat/session-tools`
- `PATCH /api/chat/session-config`
- `POST /api/model-providers/:provider/signin`
- `POST /api/model-providers/:provider/signin/:attemptId/manual`
- `POST /api/model-providers/:provider/signin/:attemptId/cancel`
- `POST /api/model-providers/:provider/signoff`
- `POST /api/chat`

See the request and response docs for the full contract.
