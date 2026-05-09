# ADR: Backend-Idempotent A2A Session Allocation

## Status

Accepted

## Context

Astro now has two important A2A guardrails:

1. Astro must not create backend `AgentSession` rows from the stream runtime.
2. A2A senders should forward the full backend session serializer together with
   `runtime_session_id`.

Those changes remove one large class of failures, but they do not prevent retry storms by
themselves.

In practice, a non-deterministic caller can still do this:

1. ask the backend to allocate a target executor session
2. send an invalid or incomplete A2A payload
3. receive a validation error such as `missing_a2a_task`
4. retry by allocating another target session instead of reusing the first one
5. repeat after a timeout, formatting change, or transient runtime failure

That behavior is too weak for the intended architecture.

The deeper issue is that prompt guidance is only a soft control:

- it can encourage the caller to reuse the same target session
- it cannot guarantee that behavior
- the backend is the only component that can enforce idempotent session allocation across all
  callers and retries

For A2A, the backend already owns:

- target-agent selection inputs
- target-session allocation
- runtime access bootstrap
- the stable session identity Astro must attach to

So the backend is the correct authority to decide whether a retry should reuse an existing target
session or create a new one.

The backend already has one proven idempotent allocation primitive:

- `AgentSessionHandle`
- `AgentSessionHandle.current_session`
- short transactional `select_for_update()` reuse/create behavior

That primitive is already used by the Astro command-center control-plane path.

Relevant existing backend surfaces include:

- `AgentSession`
- `AgentSessionHandle`
- `AgentSessionHandle.current_session`
- `SESSION_HANDLE_UNIQUE_ID_ASTRO_COMMAND_CENTER`
- `_get_or_create_astro_command_center_session(...)`
- `POST /orm/api/agents/v1/agents/session-handles/get_or_create_astro_command_center/`

So the A2A hardening should reuse the same primitive rather than inventing a second allocation
system.

## Problem

Without backend hardening, the system can allocate multiple executor sessions for what is really one
logical A2A task.

Typical triggers include:

- payload-shape validation failures such as `missing_a2a_task`
- switching from legacy task aliases to canonical `messages`
- response-format corrections
- runtime stream timeouts
- transient runtime or network failures
- "retry to confirm success" behavior by the caller

Those retries should not silently fork the execution lineage into multiple target sessions unless
there is an explicit reason to start over.

## Decision

The backend must make A2A target-session allocation idempotent and reuse-oriented by default by
anchoring allocation on `AgentSessionHandle`.

The core rule is:

> One logical A2A task maps to one backend-allocated target session. A fresh delegated conversation
> requires a different logical A2A correlation identity.

Prompt-layer guidance remains useful, but backend behavior is authoritative.

For the implemented backend contract, the logical A2A task is the triple:

- `caller_agent_session_id`
- `target_agent_id`
- `a2a_correlation_id`

That triple resolves to one stable `AgentSessionHandle`, and therefore one stable
`handle.current_session`, by default.

This ADR is additive.

It does not replace or redefine the current constant-based Astro command-center handle workflow.
That existing path must remain backward compatible.

## Required Backend Behavior

### 1. Session allocation must be idempotent

Whatever backend control-plane API allocates the target A2A session must behave idempotently for a
single logical task by using `AgentSessionHandle` as the allocation anchor.

Repeated allocation requests for the same logical A2A task must return the same target
`AgentSession`, not silently create a fresh one.

### 2. The backend must identify a logical A2A task explicitly

The allocation request must include a stable correlation key for the logical A2A task.

Canonical request shape:

```json
{
  "caller_agent_session_id": 52,
  "a2a_correlation_id": "52:turn-184:executor-step-1"
}
```

Canonical route:

```text
POST /orm/api/agents/v1/agents/<agent_id>/allocate-a2a-target-session/
```

The target agent is the route `agent_id`.

The exact field names can vary, but the semantics must be:

- same caller session
- same target agent
- same logical A2A task / orchestration step

If the backend already has a stronger request identity from the control plane, it may use that
instead. The important point is that retries must carry a stable reuse key.

### 3. The backend must derive a deterministic handle identity

The backend must derive a deterministic A2A handle identity from:

- `caller_agent_session_id`
- `target_agent_id`
- `a2a_correlation_id`

Representative format:

```text
a2a:<caller_session_id>:<target_agent_id>:<a2a_correlation_id>
```

That value becomes the stable handle identity for the logical A2A task.

### 4. Allocation response must be canonical

The allocation response should return:

```json
{
  "agent_session_id": 87,
  "allocation_state": "created_new" | "reused_existing",
  "session": { "...full backend AgentSession serializer..." }
}
```

This makes two things explicit:

1. whether the backend created or reused the target session
2. the exact full backend session JSON that the caller must forward to Astro under `session`

### 5. Retry classes that must reuse the same target session

The backend must treat the following as same-session retry territory by default:

- request-shape validation failures such as `missing_a2a_task`
- canonical `messages` / legacy payload normalization mistakes
- response-format corrections
- transport disconnects
- runtime stream timeouts
- transient runtime-access or health-check failures after allocation
- transient executor/runtime failures that do not make the session unrecoverable

The existence of one of those failures is not, by itself, permission to allocate another target
session.

### 6. New conversation policy

If the caller wants a fresh delegated conversation, it must allocate a new logical A2A task
identity.

That means:

- same (`caller_agent_session_id`, `target_agent_id`, `a2a_correlation_id`) => same target session
- different `a2a_correlation_id` => different target session

The backend must not branch to a fresh target session for the same correlation id.

If the previously allocated session is no longer acceptable for the same correlation id, the
backend should fail clearly instead of silently allocating a sibling target session.

### 7. Timeout must not imply fresh allocation

If the caller timed out while waiting for the runtime stream, the backend default should be:

1. inspect the existing target session state
2. return the same allocated target session when it is still the valid target for that logical task

Timeout is a transport problem unless proven otherwise. It is not an automatic session-allocation
event.

### 8. Observability must show reuse versus creation

The backend should record and expose enough data to make retry storms obvious.

Recommended fields or logs:

- `caller_agent_session_id`
- `target_agent_id`
- `a2a_correlation_id`
- `allocation_state` (`created_new` vs `reused_existing`)
- `first_allocated_at`
- `last_reused_at`
- `allocation_attempt_count`

This makes it possible to explain why multiple target sessions exist and whether they were valid or
accidental.

Initial implementation may keep these fields in:

- structured logs
- `AgentSessionHandle.metadata`

If later query requirements exceed what handle metadata can support cleanly, the backend may add a
dedicated allocation-audit model in a future ADR. That is not required for the first
implementation.

### 9. Backward compatibility with existing handle workflows

This change must be additive.

The backend must keep the existing Astro command-center handle path unchanged:

- constant handle key: `SESSION_HANDLE_UNIQUE_ID_ASTRO_COMMAND_CENTER`
- current constant value: `astro-orchestrator-command-center`
- existing route:
  `POST /orm/api/agents/v1/agents/session-handles/get_or_create_astro_command_center/`

The new A2A path must be a separate allocation contract that reuses `AgentSessionHandle`, not a
replacement for the existing constant-based workflow.

## Caller Contract

Once the backend returns the target session allocation result, the caller must persist and reuse:

- `runtime_session_id`
- the full backend `session` serializer
- the same logical-task correlation key

Every resend for the same logical task should use the same backend-allocated target session unless
the caller intentionally starts a new logical A2A task with a different correlation identity.

## Relationship To Astro

Astro should still:

- require `runtime_session_id`
- prefer request-carried full backend `session` JSON
- fetch backend session authority as a fallback when request-carried metadata is absent or
  insufficient

But Astro must not be the component that decides whether a retry should allocate a fresh target
session.

That policy belongs to the backend allocation authority.

## Consequences

### Benefits

- prevents retry storms from creating multiple executor sessions for one logical task
- makes A2A retries deterministic across callers
- keeps target-session lineage understandable
- reduces wasted runtime startup and noisy failed sessions
- turns prompt guidance into an optimization rather than the only line of defense

### Costs

- backend must introduce or formalize a logical-task correlation concept
- allocation APIs need idempotent semantics instead of "always create"
- callers must preserve and resend the same correlation key on retry
- a fresh delegated conversation now requires a new logical correlation identity rather than an
  accidental retry

## Proposed Implementation Shape

### Models

Use the existing backend models:

- `AgentSession`
- `AgentSessionHandle`

No new allocation model is required for the first implementation.

### Services

Add a dedicated backend allocation service parallel to the Astro command-center path.

Representative service name:

```text
allocate_a2a_target_session(...)
```

Responsibilities:

- validate caller session and target agent
- derive deterministic A2A handle identity
- lock the matching handle with `select_for_update()`
- reuse `handle.current_session` when the reuse policy says it is still valid
- create and bind one new target session only when no prior session exists for that logical A2A
  task
- return the canonical allocation response

### Serializers

Add dedicated serializers for:

- A2A allocation request
- A2A allocation response

The response serializer should embed the existing canonical `AgentSession` serializer.

### Views

Add one additive backend endpoint for A2A allocation:

```text
POST /orm/api/agents/v1/agents/<agent_id>/allocate-a2a-target-session/
```

## Implementation Tasks

- [ ] Define the backend A2A session-allocation API contract around a stable logical-task
      correlation key.
- [ ] Make allocation idempotent for the same logical task.
- [ ] Return the full backend session serializer in the allocation response.
- [ ] Distinguish `created_new` from `reused_existing` in the allocation response or logs.
- [ ] Record `first_allocated_at`, `last_reused_at`, and `allocation_attempt_count`.
- [ ] Keep the existing Astro command-center handle path backward compatible.
- [ ] Update A2A callers to reuse the same correlation key and target session across retries.
- [ ] Keep Astro fallback behavior as a recovery path, not as the normal session-allocation policy.

## Extends

This ADR extends:

- [`adr-25-production-a2a-discovery-and-runtime-access.md`](./adr-25-production-a2a-discovery-and-runtime-access.md)
- [`adr-27-backend-only-session-initiation.md`](./adr-27-backend-only-session-initiation.md)

ADR 25 defines how non-debug A2A finds a target runtime and sends the request.
ADR 27 defines that Astro must never create the target session from the stream runtime.

This ADR adds the missing backend rule:

- retries for the same logical A2A task must reuse the same backend-allocated target session by
  default.
