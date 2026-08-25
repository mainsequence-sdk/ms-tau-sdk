# Model Provider Authentication

Provider credentials are owned and encrypted by Django. Astro receives a
provider-neutral credential only for the authenticated user/session that is
about to run.

## Status

```http
GET /api/model-providers?agent_session_uid=<session-uid>
```

## Interactive Sign In

Tau owns provider-specific OAuth and device-code execution. Callers start a
non-secret attempt for an exact AgentSession; Tau returns the provider URL and
next action, exchanges the eventual provider response, and writes the complete
credential directly to the canonical backend credential service.

```http
POST /api/model-providers/openai-codex/signin
Content-Type: application/json

{
  "agent_session_uid": "11111111-1111-4111-8111-111111111111"
}
```

The interactive response is HTTP 202 and contains an `attempt`. Follow-up
operations remain bound to the same exact AgentSession:

```http
GET  /api/model-providers/{provider}/signin/{attempt_id}?agent_session_uid={uid}
POST /api/model-providers/{provider}/signin/{attempt_id}/manual?agent_session_uid={uid}
POST /api/model-providers/{provider}/signin/{attempt_id}/cancel?agent_session_uid={uid}
```

The manual body is accepted only while the provider explicitly requests input:

```json
{
  "input": "<short-lived callback URL, code, or provider prompt response>"
}
```

API keys, access tokens, refresh tokens, complete credentials, and passwords
must not be routed through the interactive attempt input.

Attempts are runtime-process state. A runtime restart interrupts a non-terminal
attempt; callers start a new attempt after reconciliation.

## Trusted Complete-Credential Compatibility

The existing trusted-runtime operation remains backward compatible. A client
that already possesses a complete credential may include it with
`base_version`; this path is not exposed through the general MCP surface.

```http
POST /api/model-providers/openai/signin
Content-Type: application/json

{
  "agent_session_uid": "11111111-1111-4111-8111-111111111111",
  "base_version": 0,
  "credential": {
    "type": "api_key",
    "api_key": "<secret>"
  }
}
```

OAuth payloads use `type: "oauth"` and may include `access_token`, `refresh`,
and `expires`. Astro never writes provider secrets to local files.

## Sign Off

```http
POST /api/model-providers/openai/signoff
Content-Type: application/json

{
  "agent_session_uid": "11111111-1111-4111-8111-111111111111"
}
```

The backend revokes the credential and increments its version so stale writes
cannot reactivate it.

Runtime-authenticated provider status, hydrate, flush, and revoke operations
must carry the exact backend AgentSession UID. Astro never sends a User UID as
credential-owner input; Django derives the credential owner exclusively from
that authorized session. The deployed service's responsible User remains the
acting principal and is never a credential fallback.

Pre-session sign-in, API-key sync, status, and sign-off are ordinary User-JWT
self-service operations against Django. A service runtime credential cannot
select a User before an authoritative AgentSession exists.

In A2A, the child session already inherits the immediate parent session's User.
Each target runtime hydrates independently for its child session. Astro never
places provider credentials in A2A message content, session metadata, or handle
metadata.
