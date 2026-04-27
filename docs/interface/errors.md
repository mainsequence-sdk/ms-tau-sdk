# Errors

Typical error responses:

- `400` — invalid JSON, missing `messages`, or missing latest user message
- `400` — missing `agentName` or `userId`
- `400` — missing user identity for provider status/sign-in/sign-off requests. Astro accepts
  `userId`, `user_id`, `created_by_user`, `createdByUser`, supported user-id headers, Bearer JWT
  user claims, or `ASTRO_MAINSEQUENCE_USER_ID`.
- `400` — missing `runtime_session_id` when `newChat` is `false`
- `400` — missing `sessionId` for `GET /api/chat/history`
- `400` — missing `sessionId` for `GET /api/chat/diff`
- `400` — missing `sessionId` for `GET /api/chat/session-model`
- `400` — missing `sessionId` for `PATCH /api/chat/session-config`
- `400` — `invalid_session_config` when the patch payload includes unsupported or invalid config fields
- `400` — invalid `model` payload or unsupported `runConfig.reasoning_effort`
- `404` — `provider_not_supported` for unsupported `GET/POST /api/model-providers/*` provider ids
- `404` — `signin_attempt_not_found` for unknown `GET/POST /api/model-providers/:provider/signin/:attemptId*`
- `400` — missing `sessionId` for `GET /api/chat/session-tools`; missing local metadata returns
  `200` with an empty `available_tools` object
- `400` — missing `projectId` or `cwd` when starting `mainsequence-project-coder`
- `403` — `cors_origin_not_allowed` when the browser `Origin` is not listed in `ASTRO_STREAM_TRUSTED_ORIGINS`
- `404` — `model_not_found` when the requested session model is not in `GET /api/chat/get_available_models`
- `400` — `invalid_runtime_session_id` when a missing local resume session uses an id that cannot
  be queried as a backend `AgentSession.id`
- `409` — session mismatch for provided `runtime_session_id`
- `409` — `session_not_found` only when the backend authority reports that the requested
  `AgentSession.id` does not exist
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
- `409` — `diff_not_available` when the requested session is not a `mainsequence-project-coder`
  session or its frozen repo root cannot be resolved
- `404` — `session_not_found` or `history_not_available` for `GET /api/chat/history`
- `502` — `backend_session_history_reconstruction_failed` when backend `AgentSession` data cannot
  be fetched for Astro history reconstruction
- `404` — `session_not_found` or `session_checkpoint_not_found` for read endpoints that cannot
  hydrate from backend checkpoint state
- `500` — `conversation_persistence_failed` when the server cannot write history before streaming
- `500` — `model_catalog_unavailable` when `GET /api/models/catalog` fails unexpectedly
- `500` — `available_models_unavailable` when `GET /api/chat/get_available_models` fails unexpectedly
- `502` — backend agent registration failure when registration is enabled
- `502` — backend agent session creation failure
- `404` — unknown route

## CORS

The server returns:

```
Access-Control-Allow-Headers: Content-Type, Authorization, Last-Event-ID
```

When Astro rejects a browser origin, it logs a line like:

```text
[astro-cors] Rejected origin=http://localhost:3000 method=GET path=/api/chat/session-tools?sessionId=38 trusted_origins=http://localhost:5173,http://127.0.0.1:5173
```
