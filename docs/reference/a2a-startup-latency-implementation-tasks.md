# A2A Startup Latency Implementation Tasks

Status: Draft
Date: 2026-06-16

## Goal

Reduce legacy Astro A2A chat time-to-dispatch by removing serialized startup work that does not need to
block Pi runner startup.

This document records the implementation discoveries from production traces and turns them into
specific engineering tasks. Each task should be implemented and verified independently.

## Observed Startup Shape

Current A2A startup is effectively serialized:

```text
request accepted
same-session queue starts
checkpoint lease / restore / local history validation
capability materialization
provider credential hydration
warm Pi RPC runner spawn
runtime_ready sentinel
prompt dispatch
```

In observed traces, the user-visible delay before prompt dispatch is made of multiple stacked waits:

- `runtime_turn_started -> pi_launch_model_ready`: roughly 15-35s.
- `pi_launch_model_ready -> pi_preflight_completed`: roughly 43-52s.
- `warm_runner_spawned -> warm_runner_ready`: roughly 47-56s.

The first segment is especially misleading: `pi_launch_model_ready` does not mean Pi or the model
just took 15-35s to start. It is emitted after checkpoint prelaunch work has already completed.

## Task 1: Parallelize Checkpoint Prelaunch With Warm Runner Startup

### Discovery

The phase currently visible as:

```text
runtime_turn_started -> pi_launch_model_ready
```

is checkpoint prelaunch work, not Pi startup.

That work includes:

- backend checkpoint lease acquire or renew
- possible backend checkpoint bundle restore
- local Pi session history read
- local Pi session history validation/normalization
- checkpoint lease renewal timer setup

The event name is misleading because the user sees a long delay before `pi_launch_model_ready` and
reasonably assumes Pi/model launch is slow. In reality, Astro has not started the warm runner yet.

### Why The Check Exists

Checkpoint prelaunch exists for durable cross-pod safety. It is acting as both:

- a distributed write lock for the backend `AgentSession`
- a restore barrier to ensure local Pi session history is current before Pi mutates it

Without this ownership check, two pods could write the same session history concurrently, or a pod
could run against stale local `.jsonl` state after a restart/reschedule.

### Problem

The safety requirement is real, but the implementation is over-serialized.

Astro currently waits for checkpoint ownership before starting the warm runner. That means the
request pays:

```text
checkpoint prelaunch + preflight + runner startup
```

instead of overlapping independent work.

For A2A, that is the wrong critical path. Starting a runner process and proving checkpoint ownership
can happen concurrently as long as Astro does not dispatch the prompt until checkpoint ownership and
session state are safe.

### Target Flow

For eligible warm A2A turns, use this flow:

```text
request accepted
same-session queue starts
start warm runner preparation/startup as early as possible
start checkpoint prelaunch in parallel
start capability/credential preflight in parallel where safe
wait until all required barriers are ready
dispatch prompt to warm runner
```

The prompt dispatch barrier remains strict:

```text
do not send prompt until checkpoint ownership is resolved
do not send prompt until any required restore/local validation is complete
do not send prompt until provider credentials and capability paths are ready
do not send prompt until runtime_ready has been emitted
```

### Safety Boundary

Parallelizing startup is safe only if the runner does not read mutable session history before the
checkpoint restore/local validation barrier completes.

This must be verified before implementation:

- If `pi --mode rpc --session <path>` loads the session file immediately at process startup, then
  starting it before checkpoint restore can load stale state.
- If Pi only reads session state when a prompt command is sent, then starting it before checkpoint
  completion is safe.
- If Pi currently loads at startup, introduce one of these before parallelizing:
  - a Pi RPC mode that starts command I/O without loading session state until the prompt command
  - a separate `prepare_session` RPC command after checkpoint restore
  - runner restart after checkpoint restore changes session files

### Implementation Shape

Refactor the current execution path so checkpoint prelaunch is its own named promise:

```text
checkpointReady = prepareCheckpointBeforePiLaunch(ctx)
runtimeReady = getOrStartWarmRunner(...)
preflightReady = prepareWarmPiRuntime(...)

await Promise.all([checkpointReady, runtimeReady, preflightReady])
dispatchWarmRunnerTurn(...)
```

The actual implementation may need a slightly different split because `prepareWarmPiRuntime`
currently builds the prepared runtime signature using checkpoint state. That dependency should be
made explicit:

- checkpoint lease/restore/local validation is a dispatch barrier
- static runner startup should not need checkpoint state
- final compatibility check can run after checkpoint state is known
- if checkpoint state changes compatibility after runner startup, restart or refresh the runner

### Logging Requirements

Add explicit phase logs so this problem cannot hide behind `pi_launch_model_ready` again:

- `checkpoint_prelaunch_started`
- `checkpoint_prelaunch_completed`
- `checkpoint_prelaunch_failed`
- `warm_runner_startup_started`
- `warm_runner_startup_completed`
- `a2a_dispatch_barrier_wait_completed`

Each log should include `durationMs`, `agentSessionId`, `threadId`, `sessionKey`, and whether a
restore occurred.

`pi_launch_model_ready` should not be used as a proxy for checkpoint completion.

### Acceptance Criteria

- On a cold eligible A2A request, warm runner startup begins before checkpoint prelaunch completes.
- Prompt dispatch still waits for checkpoint ownership and local history validation.
- If checkpoint restore changes session files after runner startup, the runner is restarted or is
  proven not to have loaded stale history.
- The logs clearly separate checkpoint duration, preflight duration, runner startup duration, and
  dispatch barrier wait.
- A backend checkpoint failure fails the turn cleanly and releases same-session queue ownership.
- No request dispatches a prompt while checkpoint ownership is unresolved.

### Expected Latency Impact

This does not make checkpoint faster by itself. It removes checkpoint from the serial critical path.

Instead of paying:

```text
checkpoint 30s + runner startup 50s = 80s
```

the request should pay closer to:

```text
max(checkpoint 30s, runner startup 50s) = 50s
```

The exact gain depends on whether capability and provider credential preflight can also be fully
overlapped.

## Remaining Discoveries To Document

These are separate tasks and should be expanded one by one:

- Zero-capability sessions still block on backend capability checks when the hot path should skip or
  refresh asynchronously.
- Provider credential hydration can still take 20-28s and must not block when a local manifest is
  valid.
- Warm runner startup itself takes 47-56s and needs its own breakdown.
- Same-session queue state can become stale if a terminal path writes `[DONE]` without releasing the
  warm-runner turn.
- Strict JSON A2A requests should suppress reasoning by default and should never emit finish-only
  success with no JSON text.

## Task 2: Split A2A Checkpoint Modes Instead Of Forcing Durable Checkpoint On Every Call

### Discovery

Current legacy Astro A2A chat treats every request as a durable session mutation. That forces checkpoint
lease/restore/local validation before the prompt can be dispatched, even for A2A calls that are
closer to API/tool calls than interactive UI turns.

That is too expensive for common A2A usage:

```json
{
  "message": "Return a JSON with only 2 keys.",
  "response_format": { "type": "json_object", "strict": true }
}
```

For this shape, the caller often wants a deterministic response from an existing agent identity, not
necessarily a durable write into the long-lived Pi session history before every request.

### Why The Full Checkpoint Exists

The full checkpoint path is necessary when the request will mutate durable session state that must
survive pod restarts and must not be concurrently written by another pod.

Full checkpoint protects:

- canonical backend session history
- local Pi `.jsonl` consistency
- cross-pod single-writer ownership
- recovery after pod restart/reschedule
- checkpoint restore before local state is trusted

Those guarantees are valuable, but they are not free. They should be paid only when the A2A request
needs durable session mutation semantics.

### Problem

Astro currently has one behavior:

```text
all A2A requests -> durable checkpoint ownership -> local restore/validation -> prompt dispatch
```

This means even stateless or machine-response A2A calls pay the same distributed checkpoint tax as a
full UI/runtime turn.

The result is slow and confusing:

- requests can wait 15-35s before runner startup even begins
- backend checkpoint failures kill otherwise simple A2A calls
- strict JSON calls inherit durable session semantics they did not necessarily request
- checkpoint work blocks the critical path instead of being scoped to requests that need it

### Current Direction

Do not introduce request-level durability selectors inside the A2A runtime contract.

The split is now endpoint-level:

- attached A2A session runtimes use backend session semantics and preserve checkpoint/session-history
  safety
- stateless LLM passthrough uses `POST /api/llm/chat` and does not attach to a session, start Pi,
  acquire checkpoints, or persist history

This avoids a hidden mode switch inside the A2A runtime contract. If a caller wants no session
mutation, it should call the stateless endpoint instead of sending an A2A session-runtime turn.

### Safety Rules

- Attached A2A runtime turns preserve checkpoint/session-history safety.
- Stateless LLM passthrough must not mutate canonical session history.
- Stateless LLM passthrough must not acquire checkpoint leases, restore checkpoints, start Pi, or
  write conversation history.
- Backend checkpoint failures must not affect `POST /api/llm/chat`.

### Acceptance Criteria

- Attached A2A runtime turns have no request-level durability selector.
- `POST /api/llm/chat` handles strict JSON machine calls without session attachment.
- Logs distinguish attached runtime work from stateless LLM passthrough work by endpoint/event name,
  not by a mode flag.

### Expected Latency Impact

Stateless LLM passthrough removes checkpoint and Pi startup latency entirely from the request path.

Attached A2A session runtimes remain optimized through attach-once, warm runner reuse, and explicit
runtime lifecycle management.
