# Errors

Astro validation errors use FastAPI's standard `422` response. Application and
backend errors use:

```json
{
  "ok": false,
  "error": "stable_error_code",
  "message": "Human-readable message",
  "detail": null
}
```

Common status codes:

- `400`: missing user identity, missing user message, or invalid stateless JSON controls.
- `404`: backend session, task, or provider credential not found.
- `409`: runtime lease or append sequence conflict.
- `502`: backend or model provider returned an unusable response.
- `503`: backend authentication or service dependency unavailable.

Once an assistant-ui SSE response has started, execution failures are emitted as
an `error` chunk followed by `[DONE]`.
