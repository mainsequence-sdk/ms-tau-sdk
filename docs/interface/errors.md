# Errors

Typical error responses:

- `400` — invalid JSON, missing `messages`, or missing latest user message
- `400` — missing `agentName` or `userId`
- `400` — missing `runtime_session_id` when `newChat` is `false`
- `409` — session mismatch or unknown session for provided `runtime_session_id`
- `502` — backend agent registration failure when registration is enabled
- `502` — backend agent session creation failure
- `404` — unknown route

## CORS

The server returns:

```
Access-Control-Allow-Headers: Content-Type, Authorization, Last-Event-ID
```
