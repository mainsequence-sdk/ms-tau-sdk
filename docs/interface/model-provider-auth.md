# Model Provider Authentication

Provider credentials are owned and encrypted by Django. Astro receives a
provider-neutral credential only for the authenticated user/session that is
about to run.

## Status

```http
GET /api/model-providers?agent_session_uid=<session-uid>
```

## Sign In

The clean Tau API accepts a complete provider credential. Browser/device OAuth
flows belong in the Python API client or frontend and finish by calling this
endpoint.

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
