# ADR 31: Replace Backend `id` Identity With `uid`

Status: Accepted
Date: 2026-05-22
Implementation Status: Implemented in runtime and active interface/docs; focused automated test coverage still pending

## Context

Astro previously assumed that backend resource identity was primarily expressed through serializer field
`id` and backend lookup paths keyed by numeric ids.

Before this change, active examples included:

- backend `AgentSession` fetch by `/orm/api/agents/v1/sessions/{id}/`
- backend session checkpoint, restore, cancel, and insights routes keyed by `{agent_session_id}`
- backend Agent-scoped session start by `/orm/api/agents/v1/agents/{agent_id}/start_new_session/`
- request-carried backend session serializers that still expose `session.id`
- request docs that describe `runtime_session_id` as the backend `AgentSession.id`
- runtime code that validates some backend session identifiers as numeric before lookup

That contract is no longer valid for the backend direction we want.

Backend `id` is deprecated and should no longer be accepted as the lookup identity. Backend lookup
must happen by `uid`, and backend serializers must return `uid` instead of `id`.

This is a meaningful boundary change because Astro currently mixes several different identity
concepts:

- backend row/resource identity
- deterministic semantic identity such as `agent_unique_id`
- backend classification identity such as `agent_type`
- frontend/runtime attach identity such as `runtime_session_id`

The first of those must move to `uid` without weakening the others.

## Problem

If the backend stops accepting `id` and stops returning serializer field `id`, current Astro
behavior becomes incorrect in several places:

1. backend lookup helpers target the wrong routes or the wrong path parameters
2. request/session hydration logic rejects valid non-numeric backend session identities
3. request-carried backend serializers no longer match Astro's parser expectations
4. checkpoint and runtime-control clients send outdated path identity
5. docs and A2A examples continue to teach the wrong contract

There is also a naming problem: keeping request and runtime fields named `*id` while the value is
actually a backend `uid` would make the contract harder to reason about and easier to misuse.

## Decision

Astro will adopt backend `uid` as the only accepted backend resource lookup identity.

This change is strict:

- Astro will not accept backend serializer field `id` as an alias for backend resource identity.
- Astro will not accept backend lookup by numeric `id`.
- Astro will not keep dual `id` / `uid` compatibility in request parsing or backend helper code.

### Backend resource identity

Backend-facing resource identity becomes:

- `agent.uid` for backend Agent lookup/reference
- `session.uid` for backend AgentSession lookup/reference

Astro must treat those values as opaque strings. It must not require them to be numeric and must
not derive meaning from their format.

### Request and attach identity

Astro's request/runtime contract should align with the backend identity language:

- `runtime_session_uid` becomes the canonical request field for real chat and A2A attach
- request aliases such as `runtime_session_id`, `runtimeSessionId`, and `sessionId` should be
  removed from the active contract
- request-carried backend session serializers should include `uid`, not `id`

When the frontend or an A2A caller wants Astro to attach to an existing backend session, it should
provide the backend session `uid`, not a numeric id-shaped value under an `id` name.

### Session and agent serializer contract

Backend serializers consumed by Astro should expose:

- top-level session `uid`
- related agent `uid`
- existing identity fields such as `agent_type`, `agent_unique_id`, and `thread_id`

Astro should parse backend resource identity only from `uid`.

### Distinction from `agent_unique_id`

`uid` is backend resource identity.

`agent_unique_id` remains a different concept:

- deterministic semantic identity used for backend agent resolution
- stable business/runtime identity such as `astro-orchestrator_<user>` or `project-executor`

Astro must not collapse `uid` and `agent_unique_id` into one field.

### Local runtime metadata and persisted files

Astro-local metadata should align with the same naming:

- `agentUid`
- `agentSessionUid`
- stream/session metadata wire equivalents such as `agent_uid` and `agent_session_uid`

Local session filenames and local session keys should be keyed by backend session uid strings.

### Backend helper routes

All Astro backend helpers that currently target id-shaped paths must move to uid-based lookup.

That includes:

- backend session fetch helpers
- checkpoint lease / restore / flush / release routes
- runtime cancel routes
- insights update routes
- any remaining Agent-scoped or AgentSession-scoped lookup helpers

Whether the backend keeps the same URL shape with `{uid}` in the path or exposes a different route
family is a backend contract detail. From Astro's perspective, the lookup key is `uid` and `id`
must not be used.

## Non-Goals

This ADR does not:

- replace `agent_type`
- replace `agent_unique_id`
- change project/runtime profile semantics
- introduce a compatibility layer for old backend `id` serializers
- preserve numeric-only validation of session identity

## Implementation Tasks

- [x] Add a focused runtime ADR implementation pass for backend helper code that still parses or
      normalizes numeric ids.
- [x] Replace backend serializer parsing from `id` to `uid` for Agent and AgentSession payloads.
- [x] Replace backend fetch helpers so session lookup uses backend session uid, not numeric id.
- [x] Replace checkpoint client identity fields and routes from `agentSessionId` semantics to
      `agentSessionUid` semantics.
- [x] Replace any remaining Agent-scoped helper lookup that still depends on backend `agent_id`.
- [x] Remove numeric validation for attach/session hydration identity.
- [x] Rename request contract fields from `runtime_session_id` to `runtime_session_uid`.
- [x] Remove accepted request aliases `runtimeSessionId` and `sessionId` from the active attach
      contract if they exist only to preserve `id` vocabulary.
- [x] Update A2A request contract and examples to use `runtime_session_uid` plus full backend
      session serializers containing `uid`.
- [x] Rename local metadata fields from `agentId` / `agentSessionId` to `agentUid` /
      `agentSessionUid` where those fields mean backend resource identity.
- [x] Keep `agent_unique_id` unchanged and documented as distinct from `uid`.
- [x] Update request docs, session docs, A2A docs, ADR examples, and runtime logging docs to use
      `uid` consistently.
- [ ] Add focused tests for backend session hydration from `uid`-based serializers.
- [ ] Add focused tests for A2A attach using `runtime_session_uid`.
- [ ] Add focused tests confirming old backend serializer field `id` is rejected rather than
      silently accepted.

## Consequences

### Positive

- Astro will match the backend's new identity contract directly.
- Backend resource identity becomes explicit and opaque instead of accidentally numeric.
- Request/session hydration code becomes less brittle because it no longer depends on integer id
  parsing.
- The distinction between backend `uid`, backend `agent_type`, and deterministic
  `agent_unique_id` becomes clearer.

### Costs

- This is a wide contract change across runtime code, sidecar/backend clients, docs, and
  frontend/A2A request shapes.
- Existing local metadata and local session artifacts keyed purely by old `id` assumptions may need
  cleanup or explicit migration.
- Old clients and old request payloads using `runtime_session_id` or serializer field `id` will
  stop working once the strict cutover is implemented.

## Follow-Up Questions

- Should backend lookup routes remain path-based with `{uid}` or move to explicit query/body lookup
  endpoints for some resources?
- Should Astro expose both camel-case and snake-case `uid` field names locally, or should it
  standardize immediately on one wire form plus one TypeScript form?
- Does the frontend session contract want a hard rename to `runtimeSessionUid`, or should the
  browser-facing API stay snake-case first with `runtime_session_uid` only?
