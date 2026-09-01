# HTTP Interface

Astro exposes:

- assistant-ui streaming at `POST /api/chat`
- agent-targeted sessionless execution at `POST /api/agents/{agent_uid}/responses`
  and `POST /api/agents/{agent_uid}/responses/stream`
- session model reads and execution cancellation under `/api/chat`
- standard A2A REST, SSE, and JSON-RPC under `/api/a2a`
- health, OpenAPI, and Swagger UI at `/health`, `/openapi.json`, and `/docs`

See [request](./request.md), [response](./response.md),
[environment](./environment.md), [provider control](./model-provider-auth.md),
[logging](./logging.md), and [A2A](../a2a/README.md).
