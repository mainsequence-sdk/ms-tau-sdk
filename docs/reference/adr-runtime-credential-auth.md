# ADR: Runtime Credential Authentication

## Status

Accepted

## Context

Astro previously accepted token-style Main Sequence authentication inputs. That behavior no longer
matches the deployment auth contract. The deployed coding-agent service authenticates through
runtime credentials:

```env
MAINSEQUENCE_AUTH_MODE=runtime_credential
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID=...
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET=...
```

In this mode, runtime credentials are the only canonical auth input.

## Decision

Astro will treat `runtime_credential` as the only supported Main Sequence auth mode.

When `MAINSEQUENCE_AUTH_MODE=runtime_credential`:

- Astro must require `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` and
  `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`
- Astro must run runtime-credential `mainsequence login --backend ... --projects-base ...` to
  exchange the runtime credential and update the CLI auth store before verification
- Astro must preserve runtime credential env vars for all child `pi`, specialist, and project setup
  processes
- health/liveness behavior must not depend on preexisting CLI auth

## Implementation Impact

### `scripts/mainsequence_runtime_auth.ts`

This module is the single runtime-credential auth entry point.

Required changes:

- restrict auth-mode detection to `runtime_credential`
- add validation for runtime credential env
- make `bootstrapMainsequenceCliAuth(...)` run the runtime-credential login path
- make `startMainsequenceCredentialExchangeLoop(...)` re-run runtime-credential exchange when runtime
  credential mode is active
- keep `buildMainsequenceStoredAuthEnv(...)` from deleting or altering runtime credential env vars
- scrub token-style Main Sequence auth env vars before spawning child processes

Implementation detail:

- runtime credential readiness first exchanges the runtime credential through `mainsequence login`,
  then checks the resulting CLI auth store with `mainsequence user`
- backend REST registration asks the installed Main Sequence SDK/client for authorization headers
  through `mainsequence.client.utils.get_authorization_headers()`, so the Node stream does not
  guess a runtime-credential bearer-token format

### `interface/stream/server.ts`

The stream server currently runs the CLI auth bootstrap during module initialization, before
`server.listen(...)`.

Required changes:

- keep `GET /health` able to start without Main Sequence CLI auth
- run the appropriate auth gate only for endpoints that need Main Sequence access, especially
  `POST /api/chat`
- make startup failure messages auth-mode specific
- ensure runtime credential failures do not mention missing token auth

### `pi/extensions/shared/agent-registration.ts`

Backend agent registration previously assumed a direct backend `fetch(...)` auth path.

Required changes:

- replace token-only access resolution with auth-mode-aware access resolution
- prefer using the Main Sequence SDK/client runtime credential flow if it exposes authenticated
  request helpers
- if the backend APIs still require a bearer token, add a runtime-credential exchange helper once
  the backend contract is verified
- if runtime credential auth cannot produce authorization headers for these endpoints, backend
  agent registration must fail with an explicit unsupported-auth-mode message

### Child Process Environments

Runtime credentials must be passed through unchanged to:

- stream-spawned `pi` child processes
- `scripts/run_specialist.ts`
- delegated specialist runtimes
- project runtime bootstrap helpers

Any helper that strips token env vars must preserve:

```env
MAINSEQUENCE_AUTH_MODE
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET
```

Short-lived access-token values must not be frozen into long-running runtime-credential child
processes. The parent credential-exchange loop updates the shared CLI auth store, and children can
either read that store through the CLI or re-exchange the runtime credential through the SDK.

### Documentation And Deployment

Deployment guidance should describe runtime credential auth as the primary production path:

```env
MAINSEQUENCE_AUTH_MODE=runtime_credential
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID=...
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET=...
```

Docs should present runtime credentials as the production auth contract.

## Consequences

### Positive

- production pods use runtime credentials
- auth configuration matches the new Main Sequence runtime credential model
- startup failures become clearer and mode-specific
- child agent processes inherit the same service authentication model as the parent runtime

### Negative

- backend agent registration may need a new runtime-credential bearer-token exchange or SDK helper
- tests need to cover deterministic runtime-credential startup and child-process inheritance

## Verification Plan

- run the stream image with only runtime credential env vars and confirm it starts
- confirm `GET /health` works without token auth env
- confirm `POST /api/chat` runs the runtime credential auth gate
- confirm `mainsequence user` or the chosen SDK auth check succeeds in runtime credential mode
- confirm missing runtime credential id/secret produces a runtime-credential-specific error
- confirm backend agent registration either works in runtime credential mode or fails with an
  explicit unsupported-auth-mode error

## Tasks

- [x] Add auth mode detection and validation to `scripts/mainsequence_runtime_auth.ts`
- [x] Implement runtime credential bootstrap behavior
- [x] Make the credential exchange loop re-exchange runtime credentials in runtime credential mode
- [x] Update stream startup/request auth flow
- [x] Update backend agent registration auth resolution
- [ ] Verify Main Sequence SDK/client runtime credential support
- [x] Update docs and deployment env guidance
- [ ] Add smoke tests or scripted checks for runtime credential startup
