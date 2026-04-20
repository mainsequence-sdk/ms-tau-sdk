# ADR: Runtime Credential Authentication

## Status

Accepted

## Context

Astro currently assumes Main Sequence authentication is refresh-token based. On startup and before
important runtime actions, Astro runs a deterministic CLI auth gate that:

1. requires `MAINSEQUENCE_BACKEND` and `MAINSEQUENCE_PROJECTS_BASE`
2. reads `MAINSEQUENCE_ACCESS_TOKEN` and `MAINSEQUENCE_REFRESH_TOKEN` from env or persisted
   `auth.json`
3. refreshes the access token through `/auth/jwt-token/token/refresh/`
4. runs `mainsequence login --access-token ... --refresh-token ...`
5. verifies the persisted CLI auth store with `mainsequence user`

That behavior no longer matches the deployment auth contract. The deployed coding-agent service
will authenticate through runtime credentials:

```env
MAINSEQUENCE_AUTH_MODE=runtime_credential
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID=...
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET=...
```

In this mode, `MAINSEQUENCE_REFRESH_TOKEN` is not required and should not be treated as the
canonical auth input.

## Decision

Astro will treat `runtime_credential` as a first-class Main Sequence auth mode.

When `MAINSEQUENCE_AUTH_MODE=runtime_credential`:

- Astro must require `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` and
  `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`
- Astro must not require `MAINSEQUENCE_REFRESH_TOKEN`
- Astro must not call the JWT refresh endpoint
- Astro must not run token-based `mainsequence login --access-token ... --refresh-token ...`
- Astro must preserve runtime credential env vars for all child `pi`, specialist, and project setup
  processes
- health/liveness behavior must not depend on token-backed CLI auth

Token-backed auth can remain as a backward-compatible mode for local or legacy workflows, but it
must not shape the deployed runtime credential path.

## Implementation Impact

### `scripts/mainsequence_runtime_auth.ts`

This module should become the single auth-mode switchboard.

Required changes:

- add auth-mode detection for `MAINSEQUENCE_AUTH_MODE`
- add validation for runtime credential env
- make `bootstrapMainsequenceCliAuth(...)` branch by auth mode
- make `startMainsequenceRefreshLoop(...)` a no-op for runtime credential mode
- keep `buildMainsequenceStoredAuthEnv(...)` from deleting or altering runtime credential env vars
- rename or wrap token-specific helpers so their names do not imply all auth modes use refresh
  tokens

Implementation detail:

- runtime credential readiness is checked with `mainsequence user`
- backend REST registration asks the installed Main Sequence SDK/client for authorization headers
  through `mainsequence.client.utils.get_authorization_headers()`, so the Node stream does not
  guess a runtime-credential bearer-token format

### `interface/stream/server.ts`

The stream server currently runs the CLI auth bootstrap during module initialization, before
`server.listen(...)`.

Required changes:

- keep `GET /health` able to start without token-backed CLI auth
- run the appropriate auth gate only for endpoints that need Main Sequence access, especially
  `POST /api/chat`
- make startup failure messages auth-mode specific
- ensure runtime credential failures do not mention missing refresh tokens

### `pi/extensions/shared/agent-registration.ts`

Backend agent registration currently refreshes a JWT from `MAINSEQUENCE_REFRESH_TOKEN` and uses the
resulting bearer token for direct backend `fetch(...)` calls.

Required changes:

- replace refresh-token-only access-token resolution with auth-mode-aware access resolution
- prefer using the Main Sequence SDK/client runtime credential flow if it exposes authenticated
  request helpers
- if the backend APIs still require a bearer token, add a runtime-credential exchange helper once
  the backend contract is verified
- if runtime credential auth cannot produce an access token for these endpoints, backend agent
  registration must fail with an explicit unsupported-auth-mode message rather than silently asking
  for refresh tokens

### Child Process Environments

Runtime credentials must be passed through unchanged to:

- stream-spawned `pi` child processes
- `scripts/run_specialist.ts`
- `scripts/mainsequence_project_set_up_locally.ts`
- delegated specialist runtimes
- project runtime bootstrap helpers

Any helper that strips token env vars must preserve:

```env
MAINSEQUENCE_AUTH_MODE
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET
```

### Documentation And Deployment

Deployment guidance should describe runtime credential auth as the primary production path:

```env
MAINSEQUENCE_AUTH_MODE=runtime_credential
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID=...
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET=...
```

Docs should stop presenting `MAINSEQUENCE_REFRESH_TOKEN` as required for the stream service when
runtime credential mode is configured.

## Consequences

### Positive

- production pods no longer need user refresh tokens
- auth configuration matches the new Main Sequence runtime credential model
- startup failures become clearer and mode-specific
- child agent processes inherit the same service authentication model as the parent runtime

### Negative

- Astro must support two auth modes during migration unless token auth is removed outright
- backend agent registration may need a new runtime-credential bearer-token exchange or SDK helper
- tests need to cover auth-mode branching instead of assuming one deterministic login path

## Verification Plan

- run the stream image with only runtime credential env vars and confirm it starts
- confirm `GET /health` works without refresh-token env
- confirm `POST /api/chat` runs the runtime credential auth gate
- confirm `mainsequence user` or the chosen SDK auth check succeeds in runtime credential mode
- confirm missing runtime credential id/secret produces a runtime-credential-specific error
- confirm token mode still works if kept for local compatibility
- confirm backend agent registration either works in runtime credential mode or fails with an
  explicit unsupported-auth-mode error

## Tasks

- [x] Add auth mode detection and validation to `scripts/mainsequence_runtime_auth.ts`
- [x] Implement runtime credential bootstrap behavior
- [x] Make token refresh loop skip runtime credential mode
- [x] Update stream startup/request auth flow
- [x] Update backend agent registration auth resolution
- [ ] Verify Main Sequence SDK/client runtime credential support
- [x] Update docs and deployment env guidance
- [ ] Add smoke tests or scripted checks for runtime credential startup
