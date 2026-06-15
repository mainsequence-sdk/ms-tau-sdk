# Interface Overview

This interface exposes Astro over HTTP using assistant-ui's `ui-message-stream` protocol over SSE.
Clients must identify the backend `Agent.agent_type` (`agentType`) and user
(`user_uid`) on each request.
The normal entry point is `astro-orchestrator`. Project implementation runs through
`project-executor`; image-backed remote worker pods can pin that runtime with
`ASTRO_FIXED_AGENT_TYPE` plus fixed project env vars when the project image already contains the
prepared runtime.

The two deployment identities are documented in
[`../components/deployment-identities.md`](../components/deployment-identities.md). Use that page as
the source of truth for `agentType`, fixed-worker rejection behavior, and sidecar filesystem paths.

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
- `GET /api/chat/session-model`
- `PATCH /api/chat/session-config`
- `POST /api/model-providers/:provider/signin`
- `POST /api/model-providers/:provider/signin/:attemptId/manual`
- `POST /api/model-providers/:provider/signin/:attemptId/cancel`
- `POST /api/model-providers/:provider/signoff`
- `POST /api/chat`

See the request and response docs for the full contract.
See [`logging.md`](./logging.md) for the structured operational log contract.
