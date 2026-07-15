# ADR: Backend-Owned User Model Provider Credentials

## Status

Accepted. Astro implementation is wired for backend-owned provider credential status, scoped
hydrate/flush, sign-in sync, sign-off revoke, and container startup pruning.

## Context

Astro is moving to stateless pods. Provider credentials cannot live in Docker volumes, container
filesystems, host mounts, or repo-local paths.

Pi's model-provider auth flow is file-backed:

```text
PI_CODING_AGENT_DIR/auth.json
```

Pi's `AuthStorage.create()` reads and writes that file. For OAuth providers, Pi can also refresh
tokens while a model is being used, so `auth.json` may change during an active agent run.

The provider sign-in flow should remain the same from the user's point of view:

- the frontend still starts sign-in through Astro
- Astro still drives Pi/AuthStorage OAuth or API-key sign-in
- Pi-compatible credentials are still materialized as `auth.json` before Pi needs them
- only the storage authority changes: backend stores durable credentials, pods hydrate ephemeral
  credential files

## Decision

Backend owns durable user model-provider credential storage.

Astro owns the provider sign-in workflow and Pi file hydration/flush cycle.

Astro must not store user provider credentials in the global container `PI_CODING_AGENT_DIR`.
For every session or request that needs provider credentials, Astro creates a scoped pod-local Pi
agent auth directory:

```text
/session-state/pi-agent-auth/<scope_key>/
├── auth.json
├── settings.json
└── bin/
```

Astro launches Pi children with:

```env
PI_CODING_AGENT_DIR=/session-state/pi-agent-auth/<scope_key>
```

The scoped auth directory is pod-local only. It may live for the active session while the pod is
alive, but backend remains the durable source of truth.

For model execution, `<scope_key>` is the runtime session id, which is the backend
`AgentSession.id` string. For sign-in attempts that happen before a chat session exists,
`<scope_key>` is a temporary `signin-<provider>-<attempt_id>` value.

## Backend Model Shape

The backend can use an existing secret vault if one exists. If it needs a direct ORM model, the
shape should be equivalent to:

```python
class UserModelProviderCredential(CreatedByMixin, models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="model_provider_credentials",
    )
    provider = models.CharField(max_length=64)
    credential_kind = models.CharField(max_length=32)  # api_key | oauth
    encrypted_pi_credential = EncryptedJSONField()
    credential_hash = models.CharField(max_length=128)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=32, default="active")  # active | revoked
    metadata = models.JSONField(default=dict, blank=True)
    last_hydrated_at = models.DateTimeField(null=True, blank=True)
    last_flushed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
```

Constraint:

```python
models.UniqueConstraint(
    fields=["user", "provider"],
    name="unique_user_model_provider_credential",
)
```

`encrypted_pi_credential` stores the exact Pi credential object for that provider. The value is JSON
in Django code and encrypted by the field before it is written to the database:

```json
{
  "type": "api_key",
  "key": "..."
}
```

or:

```json
{
  "type": "oauth",
  "refresh": "...",
  "access": "...",
  "expires": 1770000000000
}
```

## Backend API Contracts

### Status

```http
GET /orm/api/agents/v1/model_provider_credentials/status/?created_by_user_uid=<user_uid>
```

Response:

```json
{
  "providers": {
    "openai-codex": {
      "status": "active",
      "credential_kind": "oauth",
      "version": 7,
      "credential_hash": "sha256:...",
      "last_flushed_at": "2026-04-21T10:00:00.000Z"
    }
  }
}
```

Backend action:

- require `created_by_user_uid`; return `400` if it is missing or invalid
- authorize the runtime credential for the requested user
- query the durable provider credential store for that user only
- return one entry per stored provider, keyed by provider name
- include both `active` and `revoked` credentials so Astro can distinguish never-signed-in from
  explicitly signed-off providers
- do not decrypt `encrypted_pi_credential`
- never return `pi_credential`, access tokens, refresh tokens, API keys, or any decrypted secret
- return `200` with an empty object when the user has no stored provider credentials:

```json
{
  "providers": {}
}
```

Status row shape:

```python
provider_status = {
    "status": "active",  # "active" | "revoked"
    "credential_kind": "oauth",  # "oauth" | "api_key"
    "version": 7,
    "credential_hash": "sha256:...",
    "last_flushed_at": "2026-04-21T10:00:00.000Z",
    "revoked_at": None,
}
```

Astro uses this endpoint for `GET /api/model-providers`, `GET /api/models/catalog`, and
`GET /api/chat/get_available_models` so the frontend can show whether each auth-backed provider is
usable before launching Pi.

### Hydrate

```http
POST /orm/api/agents/v1/model_provider_credentials/hydrate/
```

Request:

```json
{
  "created_by_user_uid": "00000000-0000-4000-8000-000000000123",
  "agent_session_id": 52,
  "providers": ["openai-codex"],
  "holder_id": "pod/astro-pi-stream-abc"
}
```

Response:

```json
{
  "credentials": {
    "openai-codex": {
      "status": "active",
      "credential_kind": "oauth",
      "version": 7,
      "credential_hash": "sha256:...",
      "pi_credential": {
        "type": "oauth",
        "refresh": "...",
        "access": "...",
        "expires": 1770000000000
      }
    }
  },
  "missing": [],
  "revoked": []
}
```

Backend action:

- authorize the pod/runtime credential for the requested user and session
- treat `holder_id` as the runtime caller identity for audit/logging, not as a replacement for
  `agent_session_id`
- decrypt only the requested active providers
- update `last_hydrated_at`
- return Pi-compatible credential objects

### Flush

```http
POST /orm/api/agents/v1/model_provider_credentials/flush/
```

Request:

```json
{
  "created_by_user_uid": "00000000-0000-4000-8000-000000000123",
  "agent_session_id": 52,
  "provider": "openai-codex",
  "base_version": 7,
  "reason": "oauth_refresh",
  "pi_credential": {
    "type": "oauth",
    "refresh": "...",
    "access": "...",
    "expires": 1770000000000
  }
}
```

Response:

```json
{
  "provider": "openai-codex",
  "status": "active",
  "version": 8,
  "credential_hash": "sha256:...",
  "accepted": true
}
```

Backend action:

- require integer `base_version`; `null` is invalid
- treat `base_version: 0` as the first write for a user/provider credential that does not exist yet
- reject if the credential is revoked
- reject if `base_version` is stale
- accept `agent_session_id: null` for sign-in/API-key sync before a chat session exists
- validate only storage/security constraints and Pi credential shape
- encrypt and persist the Pi credential object
- increment `version`
- update `credential_hash` and `last_flushed_at`

Flush reasons:

```text
signin_completed
api_key_synced
oauth_refresh
stream_finish
stream_error
shutdown_flush
```

### Revoke

```http
POST /orm/api/agents/v1/model_provider_credentials/revoke/
```

Request:

```json
{
  "created_by_user_uid": "00000000-0000-4000-8000-000000000123",
  "provider": "openai-codex",
  "reason": "user_signoff"
}
```

Response:

```json
{
  "provider": "openai-codex",
  "status": "revoked",
  "version": 9,
  "revoked_at": "2026-04-21T10:00:00.000Z"
}
```

Backend action:

- mark credential revoked
- increment `version`
- reject later stale flushes from pods holding older versions

## Astro Runtime Flow

### Before Provider-Backed Model Use

1. Resolve `created_by_user_uid`, `agent_session_id`, and selected provider.
2. Create `/session-state/pi-agent-auth/<runtime_session_id>`.
3. Call backend `hydrate` for the selected provider.
4. Write `auth.json` with only the requested provider credential.
5. Write or copy the minimal Pi runtime files needed by that scoped auth dir.
6. Launch Pi with scoped `PI_CODING_AGENT_DIR`.

### Sign-In

Existing Astro sign-in endpoints stay the frontend contract.

After Pi/AuthStorage writes the credential locally:

1. Astro reads the provider entry from scoped `auth.json`.
2. Astro calls backend `flush` with reason `signin_completed` or `api_key_synced`.
3. Astro records the returned backend `version` for later flushes.

### OAuth Refresh During Agent Work

Because Pi may refresh OAuth credentials during `getApiKey()`:

1. Astro watches scoped `auth.json` by hash/mtime.
2. When the provider credential changes, Astro flushes the changed provider to backend.
3. Astro uses the last accepted backend version as `base_version`; if the provider has no accepted
   backend version yet, Astro sends `base_version: 0`.
4. If backend rejects a stale flush, Astro reloads from backend before retrying.

### Sign-Off

Existing Astro signoff endpoint stays the frontend contract.

On signoff:

1. Astro calls backend `revoke`.
2. Astro removes the provider from scoped `auth.json`.
3. Astro rejects active use of the revoked provider for that user until a new sign-in completes.

## Astro API Contract

Provider status:

```http
GET /api/model-providers?created_by_user_uid=<user_uid>
```

Sign-in:

```http
POST /api/model-providers/{provider}/signin
```

```json
{
  "created_by_user_uid": "00000000-0000-4000-8000-000000000123",
  "agent_session_id": null
}
```

Sign-off:

```http
POST /api/model-providers/{provider}/signoff
```

```json
{
  "created_by_user_uid": "00000000-0000-4000-8000-000000000123"
}
```

Provider credential routes require the public user uid as `created_by_user_uid`. Astro sends that
same field to the backend credential API.

## Security Rules

- Backend encrypts credentials at rest.
- Backend never returns decrypted credentials from status endpoints.
- Hydrate responses are allowed only to trusted pod/runtime credential callers.
- Astro must redact credential payloads from logs.
- `auth.json` exists only under pod-local scoped auth dirs.
- Scoped auth dirs are cleaned up when the session ends or the pod starts.
- Main Sequence runtime credentials are separate from user model-provider credentials.

## Tasks

- [ ] Add backend `UserModelProviderCredential` storage or map this contract onto an existing
      backend secret vault.
- [ ] Add backend status, hydrate, flush, and revoke endpoints exactly as specified above.
- [ ] Add backend encryption-at-rest, optimistic `version` checks, revoke rejection, and no-secret
      response logging.
- [x] Add Astro `ModelProviderCredentialClient` for status/hydrate/flush/revoke.
- [x] Add scoped Pi auth directory creation under `/session-state/pi-agent-auth/<scope_key>`.
- [x] Change provider-backed Pi launches to use scoped `PI_CODING_AGENT_DIR`.
- [x] Read backend provider status for model discovery and hydrate selected credentials before Pi launch.
- [x] Flush credential changes after sign-in, API-key sync, OAuth refresh, stream finish/error, and
      shutdown.
- [x] Check scoped `auth.json` hash during provider-backed sessions and flush when changed.
- [x] Change `GET /api/model-providers` to read backend-owned status instead of local `auth.json`.
- [x] Change signoff to call backend revoke and remove the local scoped credential.
- [ ] Add tests for pod recreate hydrate, OAuth refresh flush, signoff revoke, stale flush reject,
      and no cross-user credential visibility.
