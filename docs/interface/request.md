# Request Contract

## Durable assistant-ui chat

`POST /api/chat` requires an existing backend session UID and a user message:

```json
{
  "sessionUid": "8fbc7a53-32e6-4eb8-9995-3b8040e2e314",
  "messages": [
    {
      "role": "user",
      "content": [{"type": "text", "text": "Summarize this project."}]
    }
  ]
}
```

Accepted session aliases are `sessionUid`, `runtime_session_uid`,
`runtimeSessionUid`, and `agent_session_uid`. A top-level string `message` can
be used instead of `messages`.

Astro attaches to the existing session. It never allocates a backend session
from the chat request.

## Caller identity headers

`POST /api/chat`, A2A `message:send`, `message:stream`, the JSON-RPC message
methods, and task turns require the gateway-verified caller identity headers
defined by tdag-django ADR-0043. The platform gateway resolves them from the
runtime access token and blanks any client-supplied copy; Astro never derives
the caller from the route, the request body, A2A `metadata`, or the Pi
envelope.

| `X-Caller-Kind` | Required with it | Must be absent |
| --- | --- | --- |
| `user` | `X-User-UID` (canonical UUID); `X-Username` optional, at most 255 characters | `X-Caller-Agent-UID`, `X-Caller-Coding-Agent-Service-UID`, `X-Caller-Agent-Session-UID` |
| `agent` | `X-Caller-Agent-UID` and `X-Caller-Coding-Agent-Service-UID` (canonical UUIDs); `X-Caller-Agent-Session-UID` optional, canonical UUID when present | — |

Astro validates the headers before it loads, locks, or writes anything for
the session. A missing or invalid set is rejected with HTTP `403`:

```json
{"detail": "Missing or invalid caller identity headers.", "code": "runtime_caller_identity_invalid"}
```

On `/api/a2a/rpc` the same rejection is a JSON-RPC error with code `-32000`,
the same message, and `data.code` set to `runtime_caller_identity_invalid`.
No turn starts and nothing is persisted on rejection. There is no
compatibility fallback: a request without `X-Caller-Kind` is rejected.

The accepted identity is stamped into the session as the per-turn provenance
custom entry described in [sessions.md](sessions.md). Health, readiness,
version, session-model, task read, cancel, and subscribe routes do not require
these headers.

## Agent-targeted sessionless response

`POST /api/agents/{agent_uid}/responses` accepts the canonical A2A message
envelope without `contextId` or any other session identity:

```json
{
  "message": {
    "messageId": "msg-1",
    "role": "ROLE_USER",
    "parts": [{"text": "Return a short status."}]
  },
  "configuration": {
    "acceptedOutputModes": ["text/plain"],
    "responseKind": "message"
  },
  "metadata": {
    "https://mainsequence.ai/a2a/extensions/agent-inference/v1": {
      "provider": "openai",
      "model": "gpt-5.4",
      "thinking": "medium",
      "timeoutSeconds": 120
    }
  }
}
```

Provider, model, and thinking inherit the deployment's immutable Agent snapshot
when omitted. The request creates no session, task, transcript, or checkpoint,
and it is outside the caller identity requirement above because it stamps no
session entry. Use the standard output-contract metadata extension for strict
JSON.
