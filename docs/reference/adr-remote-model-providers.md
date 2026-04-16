# ADR: Remote Auth State for Model Providers

Status: accepted

Date: 2026-04-15

## Context

Astro already supports model discovery across multiple sources.

Some of those sources require provider authentication, such as:

- OpenAI
- Anthropic
- other Pi-backed providers that depend on auth state in the runtime

Other sources, such as Ollama, are not primarily auth-driven and should not shape this ADR.

Today Astro can discover Pi-backed models through runtime auth/config state, but it does not expose
a remote HTTP contract for model-provider authentication state.

That creates two problems for the frontend:

- it cannot tell which auth-backed providers are currently usable
- it cannot remotely sign a provider in or off for model use

This ADR is intentionally only about auth for model providers.

It is not about:

- platform login
- platform backend auth
- storing secrets from the UI

For this ADR, provider secret entry and storage stay out of scope.
Astro does not become a credential-entry or credential-storage system for provider secrets.

The problem we want to solve is narrower:

- expose auth state for model providers like OpenAI and Anthropic
- allow Astro to perform Astro-managed provider signin when a provider supports it
- allow Astro to sign a provider off from runtime auth state
- make model discovery expose that auth state without erasing known models

We also want a clear meaning for "sign off of a model".
For this ADR, that means signing the provider off from Astro's runtime auth state, not deleting
upstream credentials and not clearing a session model selection.

## Decision

Astro will introduce a dedicated control plane for auth-backed model providers.

This control plane is separate from:

- `POST /api/chat`
- per-message chat payloads
- raw provider secrets

The accepted conceptual model is:

- providers have auth state in the Astro/Pi runtime
- sessions have model state
- signing a provider off removes usability, not model visibility
- signing a provider off is not the same action as clearing a session model

## Scope

### In scope

- listing auth-backed model providers known to Astro
- showing whether a provider can use Astro-managed signin and is authenticated in runtime state
- signing a provider in through Astro-managed provider signin
- signing a provider off by clearing runtime auth state
- making auth-backed model discovery surface auth state separately from model visibility
- surfacing a clear error when a signed-off provider is still referenced

### Out of scope

- collecting provider secrets from the UI
- storing provider secrets outside the existing runtime auth store
- platform auth or backend auth
- unauthenticated model sources except where discovery must coexist with them

## Current State

Today Astro can discover auth-backed models indirectly through Pi runtime state, but there is no
dedicated HTTP API for provider auth state.

Current behavior:

- provider credentials may exist in the environment
- provider auth may exist in runtime auth storage
- `GET /api/chat/get_available_models` can discover runtime-available auth-backed models from runtime state
- `GET /api/models/catalog` can expose the full Pi-registry model catalog regardless of current auth usability
- there is no UI-facing contract for "provider is configured but not authenticated"
- there is no remote contract for "sign this provider in from env"
- there is no remote contract for "sign this provider off"

That means the frontend can see discovered models, but it cannot manage the auth state that makes
those models usable.

## Proposed API

### Read model-provider auth state

- `GET /api/model-providers`

This endpoint returns auth state for providers that require authentication to expose models.

Recommended response shape:

```json
{
  "version": 1,
  "providers": [
    {
      "provider": "openai",
      "authKind": "api_key",
      "signInAvailable": true,
      "authenticated": true,
      "authSource": "runtime_store",
      "knownModelCount": 3,
      "usableModelCount": 3,
      "lastValidatedAt": "2026-04-15T10:00:00.000Z"
    },
    {
      "provider": "anthropic",
      "authKind": "api_key",
      "signInAvailable": true,
      "authenticated": false,
      "authSource": null,
      "knownModelCount": 4,
      "usableModelCount": 0,
      "lastValidatedAt": "2026-04-15T10:00:00.000Z"
    },
    {
      "provider": "openai-codex",
      "authKind": "oauth",
      "signInAvailable": true,
      "authenticated": false,
      "authSource": null,
      "knownModelCount": 6,
      "usableModelCount": 0,
      "lastValidatedAt": "2026-04-15T10:00:00.000Z"
    }
  ]
}
```

Field meaning:

- `authKind`: the provider uses either API-key auth or OAuth-style runtime auth
- `signInAvailable`: frontend-friendly flag for whether `POST .../signin` can start right now
- `authenticated`: the provider is currently active in runtime auth state
- `authSource`: where Astro is currently reading usable auth from
- `knownModelCount`: how many models Astro knows for that provider
- `usableModelCount`: how many of those models are currently usable

### Sign a provider in

- `POST /api/model-providers/:provider/signin`

This endpoint does not accept raw secrets from the client.
Instead, Astro uses its managed signin path for providers that support one and syncs the resulting
auth into runtime state so Pi-backed discovery can use it.

Recommended response shape:

```json
{
  "provider": "openai",
  "authenticated": true,
  "updatedAt": "2026-04-15T10:05:00.000Z"
}
```

Failure example:

```json
{
  "error": "provider_signin_not_available",
  "message": "openai-codex cannot be signed in through Astro's current signin flow."
}
```

### Sign a provider off

- `POST /api/model-providers/:provider/signoff`

This signs the provider off from runtime auth state without mutating the environment.

Signing a provider off means:

- `GET /api/models/catalog` should still be able to list known models from that provider
- those models must be marked as not currently usable
- new session execution using that provider must fail clearly unless the provider is re-authenticated
- existing sessions that still reference the provider must fail clearly on the next turn

Recommended response shape:

```json
{
  "provider": "anthropic",
  "authenticated": false,
  "updatedAt": "2026-04-15T10:06:00.000Z"
}
```

## Runtime Semantics

### Auth state

Each auth-backed provider has three relevant states:

- known to Astro or not
- Astro-managed signin available or not
- authenticated in runtime state or not

A provider's models may still appear in `GET /api/models/catalog` when Astro knows that
provider, even if the provider is not currently authenticated.

`GET /api/chat/get_available_models` remains the runtime availability endpoint and should only
return auth-backed models that are executable right now.

For auth-backed models, Astro should distinguish:

- known: the model belongs to a provider Astro recognizes
- authenticated: the provider is active in runtime auth state
- usable: the model can be executed right now

For auth-backed providers, a model is usable when the provider is authenticated in runtime state.

### Sign-in behavior

Provider sign-in should:

- use Astro's provider-specific signin path when one exists
- validate that the required env inputs exist
- write or refresh runtime auth state for that provider
- verify that the provider becomes discoverable through the normal model-discovery path

### Sign-off behavior

Provider sign-off should:

- remove the provider's runtime auth state
- leave environment variables untouched
- keep known models visible in discovery when Astro can still enumerate them
- remove those models from the runtime-available list while keeping them visible in the global catalog

This is important because the environment remains the source of credentials, while Astro still needs
a first-class runtime sign-off operation.

### Session behavior

Signing a provider off does not mutate historical session data.

If a session still references a signed-off provider, the next turn should fail with a clear runtime
error instead of silently falling back.

Recommended error shape:

```json
{
  "error": "provider_not_authenticated",
  "message": "The selected model provider is not currently authenticated."
}
```

## Discovery Relationship

`GET /api/chat/get_available_models` remains the model inventory endpoint.

For auth-backed providers, its inputs become:

- Astro-managed provider signin availability
- provider auth state present in Astro/Pi runtime state

This keeps the contract clean:

- auth endpoints manage auth state
- model discovery returns known models plus their current auth and usability state

Recommended extension for auth-backed model records:

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4.5",
  "auth": {
    "required": true,
    "authKind": "api_key",
    "signInAvailable": true,
    "authenticated": false,
    "usable": false
  }
}
```

## Persistence

Because Astro should not create a second secret store, the public contract must stay focused on
runtime auth state rather than secret source details.

Astro should reuse or update the existing runtime auth store that Pi-backed discovery already
depends on.

Recommended persisted runtime state:

- provider id
- provider auth record in the runtime auth store
- validation timestamp when useful

Astro should not persist raw provider secrets anywhere outside that existing runtime auth path.

## Consequences

### Positive

- the UI gets a clear contract for auth-backed model providers
- providers like OpenAI and Anthropic become remotely inspectable and controllable
- Astro can support runtime sign-in and sign-off without becoming a credential vault
- logout no longer hides known models from the UI
- `get_available_models` reflects only the models that are actually usable for runtime execution

### Negative

- Astro now owns a runtime auth control plane for providers
- the environment may still contain valid credentials even after Astro signs the provider off
- model discovery becomes a little richer because visibility and usability are separate concepts
- active sessions must handle provider-not-authenticated failures explicitly

## Rollout

### Phase 1

- add read-only provider auth-state endpoint
- define provider env requirements per auth-backed provider

### Phase 2

- add sign-in and sign-off endpoints for auth-backed model providers
- extend `GET /api/chat/get_available_models` with auth and usability metadata

### Phase 3

- surface clear runtime errors when a signed-off provider is still referenced by a session

## Tasks

- [x] Add an ADR for auth-backed model providers.
- [x] Add `GET /api/model-providers`.
- [x] Add `POST /api/model-providers/:provider/signin`.
- [x] Add `POST /api/model-providers/:provider/signoff`.
- [x] Define env requirements for auth-backed model providers such as OpenAI and Anthropic.
- [x] Reuse the runtime auth store for provider sign-in and sign-off.
- [x] Extend `GET /api/chat/get_available_models` with auth and usability metadata for auth-backed models.
- [x] Surface a clear runtime error when a signed-off provider is still referenced by a session.
