# ADR: Hydrate Astro Local Orchestrator Session State From Backend-Owned Sessions

## Status

Accepted for the initial attach path. Session continuity and history reconstruction are now
completed through [`adr-emptydir-session-checkpoint-storage.md`](./adr-emptydir-session-checkpoint-storage.md).

## Context

Astro currently uses the backend `AgentSession.id` as the visible `runtime_session_id`, but resume
still depends on Astro's own local session files under `ASTRO_STREAM_SESSION_DIR`.

That means Astro can only resume a session when it already has local runtime artifacts such as:

- `<runtime_session_id>.meta.json`
- `<runtime_session_id>.jsonl`
- `<runtime_session_id>.conversation.jsonl`
- `<runtime_session_id>.history.json`

Previously, if the backend created an `AgentSession` that Astro did not start itself, Astro could
not attach to it from the session id alone. The old behavior was:

- `session_not_found` when no local session files exist, even if the backend session might exist
- `session_metadata_missing` when the local metadata file is missing

That gap is real, but the first implementation target is narrower than "all agents." This ADR is
only for hydrating backend-owned `astro-orchestrator` sessions.

Many fields Astro persists in local metadata are not universally required for hydration:

- some are Astro-authored session lifecycle state
- some are project-scoped specialist state
- some can be recomputed from the request or backend record

In particular, the following fields should **not** be treated as required for first-pass
orchestrator hydration:

- `projectId`
- `cwd`
- `repoRoot`
- `pendingOnboarding`
- `pendingRuntimeBootstrap`
- `switchSummary`
- `projectRuntime`
- `sessionConfigOverrides`

Those fields matter for `mainsequence-project-coder`, but that is a different problem. Project
handoff state is created by Astro's own orchestration/bootstrap flow and should not be guessed from
a backend session that Astro did not start.

## Decision

Astro will support **session hydration** for backend-owned `astro-orchestrator` sessions that
already exist but do not yet have local Astro runtime state.

When a request provides `runtime_session_id` but the corresponding local session files do not
exist, Astro will treat that as a candidate **attach-to-existing-backend-session** flow, not as a
new session flow.

This attach/hydrate path must run inside the existing `POST /api/chat` request path. It must not
introduce a second API route, a second request round-trip, or any new required frontend fields.

Given a backend `AgentSession.id`, Astro will:

1. Treat the backend session record as the canonical source of identity.
2. Fetch the backend `AgentSession` record when the local runtime session is missing.
3. Validate that the session is an `astro-orchestrator` session.
4. Reconstruct the minimum required local Astro metadata for an orchestrator session.
5. Materialize local Astro session files keyed by the backend `AgentSession.id`.
6. Continue the existing local resume/stream path against that hydrated local state.

Hydration is only valid when Astro can recover enough deterministic metadata to resume an
`astro-orchestrator` session safely.

This path must:

- **not** call agent `get_or_create`
- **not** call backend `start_new_session`
- **not** emit a frontend `new_session` event
- **not** require any new request payload beyond what the current `/api/chat` request already
  provides

This path is an attach/hydrate branch, not a backend session creation branch.

## Accepted Hydration Contract

Astro may hydrate a session when all of the following are true:

- backend registration is enabled
- the requested `runtime_session_id` is a backend `AgentSession.id`
- the backend session record exists
- the backend session record resolves to `workflow_key = astro-orchestrator`
- the backend session record exposes enough metadata to recover the orchestrator runtime contract

At minimum, Astro must recover or derive:

- `agentName = astro-orchestrator`
- `agentSessionId`
- `agentId`
- `threadId`

Astro may additionally recover:

- `sessionModelBinding`

Astro may derive without backend storage:

- `agentUniqueId` from the incoming request user id plus `agentName`

Hydration for `mainsequence-project-coder` is explicitly out of scope for this ADR and must fail
closed.

Hydration does not require a separate backend agent lookup when the backend `AgentSession` contract
already includes the related `agent.id`. An additional backend `GET agent` call is only acceptable
as a fallback if the `AgentSession` payload is missing required agent identity fields.

## Hydration Source of Truth

Astro will derive local session metadata primarily from the backend `AgentSession` record and
`session_metadata`.

Expected backend fields:

- `id`
- related `agent`
- `thread_id`
- `llm_provider`
- `llm_model`
- `session_metadata.workflow_key`
- `session_metadata.session_model_binding` when present as cached Astro runtime state

Astro may derive some fields from stable runtime context when needed:

- `agentUniqueId` from the requested user id plus `agentName`

Astro must not invent project-local specialist metadata during orchestrator hydration.

The current request already supplies the runtime context needed for this branch:

- `messages` / `latestUserMessage`
- `agentName`
- `userId`
- `runtime_session_id`
- optional full backend `AgentSession` serializer under `session`
- optional `threadId`

Hydration must reuse that same request. The user message that triggered the attach attempt must
remain the active turn and must continue through the normal stream flow after local state is
materialized.

## Local Materialization Rules

Hydration will create the same local runtime artifacts Astro normally writes during session
creation:

- `<sessionId>.meta.json`
- `<sessionId>.conversation.jsonl`
- `<sessionId>.history.json`

`<sessionId>.jsonl` may be created lazily if Pi continuation needs it.

The minimal successful orchestrator hydration may start with:

- `.meta.json`
- empty or placeholder local conversation/history files

Astro can then append subsequent turns normally.

The attach/hydrate branch should reuse the existing local stream flow after the backend session
creation branch, including:

- `writeSessionMetadata(...)`
- `writeThreadBinding(...)`
- `createConversationStore(...)`

The only part it must skip is the backend session creation call.

After local metadata is materialized, the same request should continue to:

- record the current user message in the conversation store
- open the SSE stream
- build the prompt
- run Pi normally

When the request already carries the backend `AgentSession` serializer, Astro should prefer that
request-carried session authority over performing a separate backend session fetch just to recover
model/provider identity or wrapper metadata.

Hydration must not discard or replay the triggering user message through a second request.

Hydration must be idempotent:

- repeated hydration of the same backend session must not fork identity
- if local metadata already exists, Astro should use it unless the user explicitly requests repair
  or migration behavior

## Failure Rules

Hydration must fail with a precise error when:

- the backend session does not exist
- the provided runtime session id cannot be queried as a backend `AgentSession.id`
- backend lookup is disabled, so Astro cannot ask the backend authority
- the backend session exists but does not resolve to `workflow_key = astro-orchestrator`
- the backend session exists but required orchestrator metadata is missing
- the recovered session agent does not match the requested agent
- no `threadId` can be recovered from backend metadata or the current request

Hydration should return a dedicated error such as:

- `session_hydration_failed`
- `invalid_runtime_session_id`
- `session_hydration_unavailable`

instead of overloading:

- `session_not_found`
- `session_metadata_missing`

when the backend session exists but could not be reconstructed safely.
`session_not_found` should only be returned after the backend authority reports that the requested
`AgentSession.id` does not exist.

## Consequences

### Positive

- backend `AgentSession.id` becomes the real source of truth for orchestrator session
  recoverability, not only identity
- Astro can attach to orchestrator sessions it did not originally create
- frontend resume flows become more robust across restarts, migrations, and multi-writer session
  creation
- the implementation stays close to the current stream runtime shape because it reuses the existing
  local resume path after materializing local files

### Negative

- Astro now depends on a backend read path for session hydration
- hydration logic must map backend metadata carefully into Astro-local semantics
- partially specified backend sessions can no longer be treated as opaque success cases
- this ADR does not solve `mainsequence-project-coder` hydration
- empty local history after hydration may not reflect older turns unless a later history-sync step
  is added
- the stream runtime gains a third branch: create new session, resume existing local session, or
  attach/hydrate backend-owned session

## Non-Goals

- Do not treat the backend as the source of truth for local conversation history formatting yet.
- Do not attempt full Pi session reconstruction from backend message history in the first step.
- Do not hydrate `mainsequence-project-coder` in this first pass.
- Do not silently coerce malformed specialist/project metadata into a valid coder session.

## Rollout Plan

1. Add a backend session fetch helper by `AgentSession.id`.
2. Introduce an `attachHydratedBackendSession(...)` helper in the stream runtime.
3. Call hydration from the resume path when:
   - `runtime_session_id` is provided
   - local session files are missing
   - backend registration is enabled
4. Only proceed when the backend session resolves to `workflow_key = astro-orchestrator`.
5. Do not call `registerMainsequenceAgent(...)` or `startBackendAgentSession(...)` on this path.
6. Reuse the existing request-scoped `latestUserMessage`, `agentName`, `userId`, and optional
   `threadId`; do not require any new request fields.
7. Write local `.meta.json` from backend session metadata.
8. Create minimal local history/conversation files for future turns.
9. Re-enter the existing local resume/stream flow with the hydrated metadata.
10. Add dedicated telemetry/logging for hydration success and hydration failure reasons.

## Follow-up

- Add a backend read contract doc for the session fetch payload Astro expects.
- Backend checkpoint projection now backfills local `.history.json` from `bundle.pi_session_jsonl`;
  the backend does not store Astro's frontend history response shape.
- Add a repair endpoint or admin flow for rehydrating corrupted local session state from backend.
- Revisit whether thread binding should also be reconstructed from backend metadata during
  hydration.
- Write a separate ADR if backend-owned `mainsequence-project-coder` hydration is ever required.

## Tasks

- [x] Add an ADR for backend-owned session hydration.
- [x] Add a backend session fetch helper by `AgentSession.id`.
- [x] Add `attachHydratedBackendSession(...)` to the stream runtime.
- [x] Ask the backend authority before returning `session_not_found` for registered backends.
- [x] Skip `registerMainsequenceAgent(...)` on the hydration attach path.
- [x] Skip `startBackendAgentSession(...)` on the hydration attach path.
- [x] Keep hydration inside the existing `/api/chat` request path with no new request fields.
- [x] Continue the same triggering user message after hydration instead of requiring a retry.
- [x] Emit `session_hydration_failed` for recoverable backend-known / Astro-unknown failures.
- [x] Persist hydrated `.meta.json` and minimal local session files.
- [x] Reject hydration when the backend session is not `astro-orchestrator`.
