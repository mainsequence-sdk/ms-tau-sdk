# ADR 41: Remove `agent_unique_id`

Status: Accepted
Date: 2026-06-22
Implementation Status: Implemented.

This ADR supersedes the `agent_unique_id` distinction described in
[ADR 31: Backend UID Identity](./adr-31-backend-uid-identity.md). Backend Agent `uid` is now the
only Agent resource identity Astro should use.

## Context

Astro currently handles several backend/session identity concepts:

- `agent.uid`: backend Agent resource identity.
- `agent_session.uid`: backend AgentSession resource identity.
- `agent_type`: backend/product classification such as `astro-orchestrator` or `code-repository-executor`.
- `agent_unique_id`: deterministic semantic identity historically used for lookup/reuse.

The project has already moved the active runtime model toward backend-owned session attach:

- callers provide `runtime_session_uid`
- Astro hydrates the backend session by AgentSession `uid`
- the backend session payload provides the attached Agent `uid`
- Pi execution is scoped to the backend AgentSession `uid`

In that model, `agent_unique_id` is redundant and risky. It creates a second identity string for the
same Agent concept even though the backend already has an authoritative Agent `uid`.

## Problem

Keeping `agent_unique_id` creates avoidable ambiguity:

- Runtime code can accidentally compare semantic identity instead of backend resource identity.
- Public responses expose both `X-Agent-Uid` and `X-Agent-Unique-Id`, making clients wonder which
  one is authoritative.
- Checkpoint/session metadata preserves a duplicate identity that can become stale.
- The backend adapter still exposes `buildAgentUniqueId(...)`, which encourages new code to create
  semantic lookup keys instead of using backend `uid`.
- Tests preserve `code-repository-executor` unique-id special cases that no longer belong in the unified
  runtime model.

The right boundary is stricter:

```text
Agent identity        = agent.uid
AgentSession identity = agent_session.uid
Agent classification  = agent_type
```

There should not be a fourth identity axis for `agent_unique_id`.

## Decision

Astro will completely remove `agent_unique_id` from active runtime behavior, persistence, public
responses, adapter contracts, tests, and active documentation.

There is no backward compatibility path.

Astro must not:

- parse `agent_unique_id`
- parse `agentUniqueId`
- generate `agent_unique_id`
- persist `agent_unique_id`
- checkpoint `agent_unique_id`
- validate sessions by `agent_unique_id`
- expose `agent_unique_id` in SSE chunks
- expose `agent_unique_id` in public JSON responses
- expose `X-Agent-Unique-Id`
- expose `X-Agent-Uid`
- keep `buildAgentUniqueId(...)`
- keep `code-repository-executor` unique-id override behavior

The canonical public and internal fields become:

```text
agent_uid
agentSessionUid / agent_session_uid
agent_type
thread_id
runtime_session_uid
```

`agent_uid` and `agent_session_uid` are opaque backend `uid` strings. They are not numbers and must
not be parsed as numbers.

## Public Response Surface

Astro must stop emitting both Agent identity headers:

```http
X-Agent-Unique-Id
X-Agent-Uid
```

This is a separate public-contract change from removing internal metadata fields. Public stream
responses should expose the session authority, not the Agent resource identity. Clients should use:

```http
X-Agent-Session-Uid
X-Thread-Id
X-Session-Key
```

`X-Agent-Session-Uid` is the authoritative backend AgentSession identity for stream continuation.
If a client needs the Agent `uid`, it should read it from the backend session serializer/control
plane, not from Astro's stream response headers.

## Backend Adapter Contract

The backend identity capability must no longer include:

```ts
buildAgentUniqueId(...)
```

Backend adapters may still classify sessions by `agent_type`, but they must not mint or require
semantic Agent keys. Agent lookup and session attach must use backend `uid` values supplied by the
backend session authority.

## Runtime Metadata

Session metadata should remove:

```ts
agentUniqueId
agent_unique_id
```

Runtime metadata should retain:

```ts
agentId        // existing local name for backend Agent uid until renamed
agentUid       // preferred local name where available
agentSessionId // existing local name for backend AgentSession uid until renamed
agentSessionUid
agentType
threadId
```

Existing local names that still say `Id` may remain only as part of a separate naming cleanup. They
must continue to carry backend `uid` values, not numeric ids.

## Checkpoints

Checkpoint metadata must stop writing:

```json
{
  "agentUniqueId": "...",
  "agent_unique_id": "..."
}
```

Checkpoint restore must not use `agent_unique_id` to validate or hydrate identity. Restore should
trust backend AgentSession `uid` plus backend Agent `uid`.

## Request Parsing

Requests must not accept:

```json
{
  "agent_unique_id": "...",
  "agentUniqueId": "..."
}
```

Request-carried session serializers must not be allowed to provide Agent semantic identity as a
fallback. They must provide backend `uid` fields.

## Implementation Tasks

- [x] Remove `agentUniqueId` from `RequestContext`, `SessionMetadata`, and `HydratedBackendSession`.
- [x] Delete `extractRequestedAgentUniqueId(...)`.
- [x] Remove `agent_unique_id` / `agentUniqueId` parsing from request payloads, request-carried
      session serializers, backend session serializers, checkpoint bundles, and local metadata.
- [x] Remove the session mismatch check that compares `existingSessionMetadata.agentUniqueId` with
      the current `agentUniqueId`.
- [x] Stop emitting `X-Agent-Unique-Id`.
- [x] Stop emitting `X-Agent-Uid`.
- [x] Remove `agent_unique_id` from `StreamEvent.new_session`.
- [x] Stop writing `agentUniqueId` and `agent_unique_id` into session metadata.
- [x] Stop preserving `agentUniqueId` and `agent_unique_id` in the checkpoint sidecar.
- [x] Remove `buildAgentUniqueId(...)` from `BackendIdentityCapability`.
- [x] Remove Main Sequence `buildAgentUniqueId(...)` implementation and the
      `code-repository-executor` unique-id override map.
- [x] Remove tests that assert deterministic unique-id generation.
- [x] Add tests asserting the stream response does not contain `X-Agent-Unique-Id` or
      `X-Agent-Uid`.
- [x] Add tests asserting request/session payload `agent_unique_id` does not affect session
      authority.
- [x] Update active docs to remove `agent_unique_id`, `agentUniqueId`, `X-Agent-Unique-Id`, and
      `X-Agent-Uid`.

## Non-Goals

- Do not rename every local `agentId` / `agentSessionId` variable in this ADR. That is a separate
  cleanup. This ADR removes `agent_unique_id`; it does not complete the broader `id` to `uid`
  local naming cleanup.
- Do not keep compatibility aliases for old clients.
- Do not add a replacement semantic identity field.

## Consequences

Clients that read `X-Agent-Unique-Id` or `X-Agent-Uid` from Astro responses must switch to the
backend session/control-plane response. Runtime continuation should use `X-Agent-Session-Uid` or the
known backend AgentSession `uid`.

Backend/session attach becomes simpler: Astro attaches to a backend AgentSession by `uid`, then uses
the backend-provided Agent `uid`. No deterministic Agent semantic key participates in runtime
authority.

Removing `agent_unique_id` reduces identity ambiguity and aligns the runtime with the backend UID
contract.
