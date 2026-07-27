# Model Provider Authentication

Provider credentials are owned and encrypted by Django. Astro receives a
provider-neutral credential only for the authenticated user/session that is
about to run.

## Status

```http
GET /api/model-providers
X-MainSequence-User-Uid: <user-uid>
```

## Sign In

The clean Tau API accepts a complete provider credential. Browser/device OAuth
flows belong in the Python API client or frontend and finish by calling this
endpoint.

```http
POST /api/model-providers/openai/signin
X-MainSequence-User-Uid: <user-uid>
Content-Type: application/json

{
  "agent_session_uid": null,
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
X-MainSequence-User-Uid: <user-uid>
Content-Type: application/json

{}
```

The backend revokes the credential and increments its version so stale writes
cannot reactivate it.
