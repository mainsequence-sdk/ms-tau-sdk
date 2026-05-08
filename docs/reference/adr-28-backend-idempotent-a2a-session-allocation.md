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

The backend must make A2A target-session allocation idempotent and reuse-oriented by default.

The core rule is:

> One logical A2A task maps to one backend-allocated target session unless an explicit restart rule
> says otherwise.

Prompt-layer guidance remains useful, but backend behavior is authoritative.

## Required Backend Behavior

### 1. Session allocation must be idempotent

Whatever backend control-plane API allocates the target A2A session must behave idempotently for a
single logical task.

Repeated allocation requests for the same logical A2A task must return the same target
`AgentSession`, not silently create a fresh one.

### 2. The backend must identify a logical A2A task explicitly

The allocation request must include a stable correlation key for the logical A2A task.

Recommended contract:

```json
{
  "caller_agent_session_id": 52,
  "target_agent_id": 25,
  "a2a_correlation_id": "52:turn-184:executor-step-1"
}
```

The exact field names can vary, but the semantics must be:

- same caller session
- same target agent
- same logical A2A task / orchestration step

If the backend already has a stronger request identity from the control plane, it may use that
instead. The important point is that retries must carry a stable reuse key.

### 3. Allocation response must be canonical

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

### 4. Retry classes that must reuse the same target session

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

### 5. New target sessions require an explicit restart reason

The backend may create a fresh target session only when at least one of these is true:

1. the caller explicitly requests a restart, for example with `force_new_session=true`
2. the previous target session is incompatible with the intended target agent, workflow, or project
3. the previous target session is terminal and backend policy says it cannot be continued for this
   A2A task
4. the previous target session is unrecoverable according to a documented backend invariant

If a new target session is created, the backend should require or record a machine-readable
`restart_reason`.

### 6. Timeout must not imply fresh allocation

If the caller timed out while waiting for the runtime stream, the backend default should be:

1. inspect the existing target session state
2. return the same allocated target session when it is still the valid target for that logical task
3. require an explicit restart reason before branching to a new session

Timeout is a transport problem unless proven otherwise. It is not an automatic session-allocation
event.

### 7. Observability must show reuse versus creation

The backend should record and expose enough data to make retry storms obvious.

Recommended fields or logs:

- `caller_agent_session_id`
- `target_agent_id`
- `a2a_correlation_id`
- `allocation_state` (`created_new` vs `reused_existing`)
- `restart_reason` when present
- timestamps for first allocation and later reuse attempts

This makes it possible to explain why multiple target sessions exist and whether they were valid or
accidental.

## Caller Contract

Once the backend returns the target session allocation result, the caller must persist and reuse:

- `runtime_session_id`
- the full backend `session` serializer
- the same logical-task correlation key

Every resend for the same logical task should use the same backend-allocated target session unless
the backend has explicitly instructed the caller to start a new one.

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
- explicit restart semantics must be designed instead of relying on accidental fresh allocation

## Implementation Tasks

- [ ] Define the backend A2A session-allocation API contract around a stable logical-task
      correlation key.
- [ ] Make allocation idempotent for the same logical task.
- [ ] Return the full backend session serializer in the allocation response.
- [ ] Distinguish `created_new` from `reused_existing` in the allocation response or logs.
- [ ] Require an explicit restart reason before creating a fresh target session for an existing
      logical task.
- [ ] Document which terminal or incompatible states justify a forced new target session.
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
