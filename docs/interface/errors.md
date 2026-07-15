# Errors

Typical error responses:

- `400` — invalid JSON, missing `messages`, or missing latest user message
- `400` — missing `agentType` or `user_uid`
- `400` — `unknown_agent_type` when `agentType` is not one of `astro-orchestrator` or
  `project-executor`
- `400` — missing `created_by_user_uid` for available-model discovery or provider
  status/sign-in/sign-off requests. Astro accepts `created_by_user_uid`, supported user-uid
  headers, or Bearer JWT user uid claims.
- `400` — missing `runtime_session_uid` (or accepted camel-case alias) for real non-mock
  `POST /api/chat` requests
- `400` — missing `sessionUid` for `GET /api/chat/session-model`
- `400` — missing `sessionUid` for `PATCH /api/chat/session-config`
- `400` — `invalid_session_config` when the patch payload includes unsupported or invalid config fields
- `404` — `provider_not_supported` for unsupported `GET/POST /api/model-providers/*` provider ids
- `404` — `signin_attempt_not_found` for unknown `GET/POST /api/model-providers/:provider/signin/:attemptId*`
- `400` — missing required project-scoped fields such as `cwd` when starting a project-attached request
- `403` — `cors_origin_not_allowed` when the browser `Origin` is not listed in `ASTRO_STREAM_TRUSTED_ORIGINS`
- `400` — `invalid_runtime_session_uid` when a missing local resume session uses a uid that cannot
  be queried as a backend `AgentSession.uid`
- `409` — session mismatch for provided `runtime_session_uid`
- `409` — `fixed_agent_type_mismatch` when a fixed backend identity receives a different request
  `agentType`
- `409` — `session_not_found` only when the backend authority reports that the requested
  `AgentSession.uid` does not exist
- `409` — `session_hydration_unavailable` when local session files are missing but backend session
  lookup is disabled
- `409` — `session_hydration_failed` when a backend-owned `astro-orchestrator` session exists but
  Astro cannot safely reconstruct the local wrapper state from it
- `409` — `provider_env_not_configured` when `POST /api/model-providers/:provider/signin` has no runtime env credentials
- `409` — `provider_signin_not_available` when the provider is known but cannot be signed in through Astro's current signin flow
- `409` — `provider_signin_in_progress` when a second signin is started while the provider already has an active attempt
- `409` — `provider_env_not_syncable` when the provider is env-configured but cannot be synced into backend credential storage
- `409` — `signin_attempt_not_active` when manual input or cancel is sent to a finished or stale signin attempt
- `409` — `provider_not_authenticated` when chat execution uses an auth-backed provider without an active backend credential
- `503` — `provider_credentials_status_failed`, `provider_credentials_hydrate_failed`,
  `provider_credentials_flush_failed`, or `provider_credential_revoke_failed` when backend
  credential storage is unavailable or rejects the operation
- `400` — `signin_manual_input_missing` when `POST /api/model-providers/:provider/signin/:attemptId/manual` has no `input`
- `409` — resume request `projectId` or `cwd` does not match the stored session metadata
- `404` — `session_not_found` or `session_checkpoint_not_found` for read endpoints that cannot
  hydrate from backend checkpoint state
- `500` — `conversation_persistence_failed` when the server cannot write history before streaming
- `500` — `model_catalog_unavailable` when `GET /api/models/catalog` fails unexpectedly
- `500` — `available_models_unavailable` when `GET /api/chat/get_available_models` fails unexpectedly
- `502` — backend session hydration/authority fetch failure before Pi launch
- `503` — `invalid_runtime_context` when fixed runtime env is internally inconsistent, for example
  `ASTRO_FIXED_PROJECT_CWD` points to a missing directory
- `404` — unknown route

## CORS

The server returns:

```
Access-Control-Allow-Headers: Content-Type, Authorization, Last-Event-ID
```

When Astro rejects a browser origin, it logs a line like:

```text
[astro-cors] Rejected origin=http://localhost:3000 method=POST path=/api/chat trusted_origins=http://localhost:5173,http://127.0.0.1:5173
```
