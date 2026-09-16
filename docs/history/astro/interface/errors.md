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
- `403`: missing or invalid gateway caller identity headers on a message route.
  This response does not use the envelope above; its body is
  `{"detail": "Missing or invalid caller identity headers.", "code": "runtime_caller_identity_invalid"}`,
  and on `/api/a2a/rpc` it is a JSON-RPC error with code `-32000` and
  `data.code` set to the same value. See
  [request.md](request.md#caller-identity-headers).
- `404`: backend session, task, or provider credential not found.
- `409`: runtime lease or append sequence conflict.
- `502`: backend or model provider returned an unusable response.
- `503`: backend authentication or service dependency unavailable.

Once an assistant-ui SSE response has started, execution failures are emitted as
an `error` chunk followed by `[DONE]`:

```json
{"type": "error", "errorText": "provider-supplied failure message"}
```

For model-provider failures, `errorText` is copied from Tau's terminal assistant
`errorMessage`. Tau's provider adapters derive that value generically from the
provider response; Astro does not maintain provider- or status-specific error
mappings. The raw provider response body and diagnostic payload are not copied
into the assistant-ui frame. A failed terminal assistant message does not also
emit a successful `finishReason: "stop"` frame.
