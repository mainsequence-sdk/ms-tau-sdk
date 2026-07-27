# HTTP Interface

Astro exposes:

- assistant-ui streaming at `POST /api/chat`
- stateless model execution at `POST /api/llm/chat`
- model catalog and provider credential control under `/api/models` and
  `/api/model-providers`
- session model, config, and cancellation under `/api/chat`
- standard A2A REST, SSE, and JSON-RPC under `/api/a2a`
- health, OpenAPI, and Swagger UI at `/health`, `/openapi.json`, and `/docs`

See [request](./request.md), [response](./response.md),
[environment](./environment.md), [provider authentication](./model-provider-auth.md),
[logging](./logging.md), and [A2A](../a2a/README.md).
