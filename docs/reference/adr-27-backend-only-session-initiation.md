# ADR: Backend-Only Session Initiation

## Status

Accepted. Astro documentation updated; runtime implementation pending.

## Context

Astro still contains a generic stream path that can initiate backend `AgentSession` creation when a
request omits `runtime_session_id` and falls into `newChat: true`.

That behavior is too weak for the current backend-owned session architecture:

- the backend must allocate the session before Astro starts work
- the backend session id is the only stable runtime identity Astro should attach to
- the backend-controlled session bootstrap path is what unlocks the runtime/RCP token flow
- generic chat callers must remain safe when request-carried session JSON is absent
- A2A senders should still inject the full backend session serializer proactively because they
  already hold the backend allocation response for the target session
- Astro must not proceed to Pi launch with no model/provider binding only because the optional
  request-carried serializer was absent

In practice, the current fallback is harmful:

1. Astro resolves `sessionModelBinding` before it has authoritative backend session state.
2. If no request-carried serializer is attached, `sessionModelBinding` can be `null`.
3. Astro can still call backend `start_new_session` and then launch Pi.
4. Pi starts without a usable model binding or hydrated provider auth and fails with
   `No models available`.

This is especially wrong for A2A. The executor runtime should be attached to a session that the
backend already created. A2A is a communication path, not a permission for Astro to allocate a new
runtime session on behalf of the backend.

## Decision

Astro must never initiate backend `AgentSession` creation from the stream runtime.

This applies to:

- `POST /api/chat`
- `POST /api/a2a/chat`

For every real non-mock streamed run:

1. the caller must provide `runtime_session_id`
2. that value must be the existing backend `AgentSession.id`
3. Astro must attach to that existing session
4. if request-carried full backend session JSON is missing, Astro must fetch the backend session
   and derive the runtime contract from backend authority before Pi launch

`newChat` is no longer allowed to trigger session creation. It becomes a deprecated UI-only hint
that Astro must ignore for routing and allocation decisions.

## Required Runtime Behavior

### 1. No Astro-owned session creation

Astro must not call backend `start_new_session` from `/api/chat` or `/api/a2a/chat`.

That means:

- no generic "missing session id => create one" fallback
- no project-executor-specific creation exception
- no orchestrator-specific creation exception

If a caller needs a new session, that session must be created by the backend control plane first.

### 2. `runtime_session_id` is mandatory

For real execution requests, Astro must require `runtime_session_id`.

Allowed aliases remain:

- `runtime_session_id`
- `runtimeSessionId`
- `sessionId`

But at least one of them must be present on:

- `POST /api/chat`
- `POST /api/a2a/chat`

If no session id is provided, Astro must reject the request before Pi launch.

### 3. Backend is the fallback source of truth

The request-carried backend `session` serializer remains useful, but it is optional optimization,
not a correctness requirement.

Astro must resolve runtime state in this order:

1. request-carried backend session serializer, if present and sufficient
2. local hydrated metadata/checkpoint state for the same `runtime_session_id`, if present
3. backend `GET session` authority for that `runtime_session_id`

If model/provider/project/runtime identity still cannot be derived safely after backend authority
is consulted, Astro must fail the request explicitly.

Astro must not launch Pi with:

- null model binding
- missing provider identity when the session requires a remote provider
- guessed project identity

### 4. No frontend `new_session` stream event

Because Astro no longer creates sessions on the hot path, the frontend must not rely on an Astro
`new_session` SSE chunk to learn session identity.

The caller should already know the session id from the backend session-allocation step that
preceded the Astro request.

## Request Contract Changes

### `POST /api/chat`

Required for real runs:

```json
{
  "runtime_session_id": "456",
  "agentName": "astro-orchestrator",
  "userId": "user_123",
  "messages": [...]
}
```

Optional but preferred:

```json
{
  "session": { "...full backend AgentSession serializer..." }
}
```

`newChat` may still appear from older clients, but Astro must treat it as non-authoritative and it
must not cause allocation.

### `POST /api/a2a/chat`

Required for all real executor requests:

```json
{
  "runtime_session_id": "87",
  "agentName": "mainsequence-project-executor",
  "userId": "user_123",
  "session": { "...full backend AgentSession serializer..." },
  "messages": [...],
  "response_format": "..."
}
```

The backend session identity is mandatory here. A2A callers must not depend on Astro to create the
executor session.
They should also forward the full backend session serializer for that same session under `session`
on every outbound A2A request so the runtime does not fall through the backend-fallback path
unless recovery is actually needed.

## Error Contract Changes

For real non-mock requests:

- `400 missing_runtime_session_id` when no session id is provided
- `409 session_mismatch` when the provided session id does not match the requested workflow,
  project, or runtime identity
- `502 backend_session_hydration_failed` or equivalent attach failure when Astro cannot fetch or
  derive the runtime contract from backend authority

Astro should no longer expose backend agent-session creation failures on the normal chat/A2A hot
path because the stream runtime is no longer allowed to allocate sessions there.

## Consequences

### Benefits

- backend becomes the only authority that creates sessions
- runtime/RCP token bootstrap stays coupled to backend session allocation
- A2A becomes deterministic and simpler for callers
- missing request-carried session JSON no longer forces a null-model Pi launch
- frontend session identity becomes consistent across human chat and A2A

### Costs

- every caller must create the session before calling Astro
- older clients that relied on `newChat` allocation must be updated
- Astro implementation must remove the existing creation branch and tighten request validation

## Implementation Tasks

- [x] Remove Astro-owned session creation from `POST /api/chat`.
- [x] Remove Astro-owned session creation from `POST /api/a2a/chat`.
- [x] Require `runtime_session_id` on all real non-mock stream requests.
- [x] Treat request-carried `session` as optional optimization, not required authority.
- [x] Fetch backend session authority before Pi launch whenever local/request metadata is
      insufficient.
- [x] Fail fast when model/provider binding cannot be resolved safely from backend-owned state.
- [x] Stop emitting `new_session` from the hot path.
- [x] Update documentation so `newChat` no longer implies Astro session creation.

## Supersedes

This ADR supersedes the stream-runtime creation assumptions in:

- [`adr-backend-owned-agent-session-allocation.md`](./adr-backend-owned-agent-session-allocation.md)
- [`interface/stream/README.md`](../../interface/stream/README.md)
- [`docs/interface/sessions.md`](../interface/sessions.md)

The backend remains the creator of the `AgentSession` row. The additional rule from this ADR is:
Astro must not be the component that initiates that creation from chat or A2A stream requests.

The retry and reuse semantics for backend-created A2A target sessions are defined in
[`adr-28-backend-idempotent-a2a-session-allocation.md`](./adr-28-backend-idempotent-a2a-session-allocation.md).
