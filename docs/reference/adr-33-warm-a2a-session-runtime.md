# ADR 33: Warm A2A Session Runtime and Cached Preflight

Status: Accepted
Date: 2026-06-15
Implementation Status: Implemented in Astro runtime. Astro records preflight timing, caches
zero-capability materialization, reuses unchanged non-zero capability materialization by binding
signature, validates provider credential cache entries against backend version/hash, invalidates
provider credential cache on auth failure, runs capability and credential preparation concurrently
behind strict launch barriers, and routes eligible `/api/a2a/chat` turns through a session-keyed
warm Pi RPC runner with cold durable fallback.

## Context

`POST /api/a2a/chat` currently enters the same durable stream execution path as a normal cold
runtime turn. The backend owns session allocation, but Astro owns the local runtime execution path
after a caller supplies an existing `AgentSession.uid`.

A recent same-session A2A trace showed the second message for the same `agentSessionId` still paid
the full runtime launch cost:

- request/session metadata was resolved quickly
- a previous local checkpoint finalizing marker was observed and ignored
- `pi_launch_model_ready` appeared roughly 13 seconds after request start
- session capability materialization took roughly 17 seconds and returned zero bindings
- provider credential hydration took roughly 20 seconds for the same provider/session
- assistant output appeared only after those steps completed

The important point is not the message text. Any A2A message on the current path pays the same fixed
runtime tax before useful model output can begin.

## Problem

Astro currently treats "same backend session" as durable state reuse, not as a warm runtime reuse
boundary.

That creates three separate latency problems:

1. Repeated no-op preflight work

   The current runtime awaits session capability materialization on every turn even when the
   materialized result is:

   ```text
   bindingCount=0
   enabledSkillBindingCount=0
   materializedSkillCount=0
   ```

   Fetching zero bindings and recreating/removing local skill directories should not be repeated on
   every same-session message.

2. Repeated provider credential hydration

   The current runtime awaits provider credential hydration on every turn for the same
   `(userUid, agentSessionId, provider)` tuple. The scoped auth directory path may be the same, but
   Astro still blocks the hot path on backend hydrate before launching Pi.

3. Fresh Pi process per turn

   The current runtime launches a fresh `pi` process per request. Session continuity comes from
   persisted files and checkpoint state, not from a live agent process. This is robust for cold
   durable execution, but it is unacceptable for interactive A2A latency.

The result is a fixed per-turn cost that is paid even when:

- the session id is unchanged
- the agent type is unchanged
- the model/provider binding is unchanged
- the capability set is unchanged or empty
- the credential version is unchanged
- the cwd/project attachment is unchanged

## Decision

Astro should introduce a warm A2A session runtime path, backed by cached preflight state and an
explicit per-session runner lifecycle.

The target behavior is:

- first message for a session may perform cold restore/preparation
- later messages for the same compatible session reuse validated runtime preparation
- later messages should not refetch zero capabilities without invalidation evidence
- later messages should not rehydrate provider credentials without invalidation evidence
- later messages should not spawn a new Pi process when a healthy warm runner can accept the turn
- cold durable execution remains available as a fallback and recovery path

This is an Astro responsibility. The backend remains the owner of session allocation and canonical
session identity, but Astro owns whether local execution is cold or warm.

## Implemented Runtime Behavior

The implementation reduces repeated preflight cost and adds real warm process reuse for eligible
A2A turns.

Implemented behavior:

- `materializeSessionCapabilities` caches session capability materialization per backend/session
  identity and local session asset root.
- zero-capability sessions reuse an in-memory TTL cache and skip the backend capability list call
  while the cache is fresh.
- non-zero capability sessions cache the binding/content signature and reuse local materialized
  files when the backend binding signature is unchanged and local files still match expected hashes.
- capability cache misses, hits, and invalidations are exposed through `cacheHit`, `cacheReason`,
  and `cacheInvalidationReason`.
- `hydrateScopedProviderCredentials` reuses a valid scoped auth manifest for the same
  `(userUid, agentSessionId, provider)` tuple.
- provider credential cache reuse validates the scoped auth manifest, local credential file, local
  credential hash, user, session, provider, TTL, and backend version/hash status.
- provider credential cache is invalidated when local credential files change or when a provider
  auth failure is observed after a Pi run.
- `runPiPrompt` starts capability preparation and provider credential preparation concurrently,
  then waits at an explicit launch barrier before spawning Pi.
- eligible `/api/a2a/chat` turns use `pi --mode rpc` through a warm runner keyed by
  `agentSessionId`.
- warm runner compatibility is checked against persisted `PreparedSessionRuntime` identity,
  model/provider/reasoning, cwd/project, session config, capability state, provider credential
  state, checkpoint state, and runtime image revision.
- same-session A2A turns are queued so only one turn mutates the session at a time.
- checkpoint lease acquisition/restore/finalization remains per turn; the warm runner does not
  bypass backend checkpoint authority.
- incompatible, unhealthy, disabled, or unsupported warm runners fall back to cold durable
  `pi --mode json` launch with `warm_runner_cold_fallback` logging.
- warm runners have idle TTL shutdown, explicit shutdown cleanup, forced restart on incompatible
  state, and RPC command acknowledgement timeouts.
- Astro logs `pi_preflight_completed`, `pi_process_spawned`, and `pi_first_output` so same-session
  A2A latency can be measured by phase. Warm runner logs include `warm_runner_spawned`,
  `warm_runner_first_output`, `warm_runner_dispatch_started`, `warm_runner_dispatch_completed`,
  `warm_runner_restarting_incompatible`, `warm_runner_stopped`, and `warm_runner_cold_fallback`.
- warm runner startup readiness is event-driven: patched Pi RPC mode emits a `runtime_ready`
  sentinel after its stdin command loop is attached, and Astro waits for that sentinel before
  dispatching commands instead of using `get_state` as a startup probe.
- environment flags document how to disable or tune the caches:
  `ASTRO_SESSION_CAPABILITY_CACHE`, `ASTRO_SESSION_CAPABILITY_CACHE_TTL_MS`,
  `ASTRO_PROVIDER_CREDENTIAL_CACHE`, `ASTRO_PROVIDER_CREDENTIAL_CACHE_TTL_MS`,
  `ASTRO_PROVIDER_CREDENTIAL_REMOTE_CHECK`, `ASTRO_A2A_WARM_RUNNERS`,
  `ASTRO_A2A_WARM_RUNNER_IDLE_TTL_MS`, `ASTRO_A2A_WARM_RUNNER_STARTUP_TIMEOUT_MS`, and
  `ASTRO_A2A_WARM_RUNNER_RPC_TIMEOUT_MS`.

## Required Architecture

### 1. Explicit preparation state

Astro should model local per-session preparation as a first-class object instead of repeating all
preflight work inside `runPiPrompt`.

Preparation state should include:

```ts
type PreparedSessionRuntime = {
  agentSessionUid: string;
  userUid: string;
  agentType: "astro-orchestrator" | "project-executor";
  cwd: string;
  projectId: string | null;
  provider: string;
  model: string;
  reasoningEffort: string | null;
  capabilityState: PreparedCapabilityState;
  providerCredentialState: PreparedProviderCredentialState;
  checkpointState: PreparedCheckpointState;
  preparedAt: string;
  lastUsedAt: string;
};
```

This object must be invalidated instead of silently reused when any identity, model, cwd,
capability, credential, or checkpoint compatibility field changes.

### 2. Cached capability preparation

Astro should cache session capability materialization per `agentSessionId`.

For zero capabilities, Astro should be able to skip the backend list call and local materialization
on later turns when no invalidation evidence exists.

For non-zero capabilities, Astro should cache:

- binding count
- enabled skill binding count
- content hashes
- materialized skill paths
- latest binding/update marker available from the backend response
- local materialization root

Astro must not fire-and-forget capability materialization for a turn that depends on those skills.
If skills are needed for the current model invocation, the materialized skill paths must be ready
before Pi starts or before a warm runner accepts the turn.

### 3. Cached provider credential preparation

Astro should cache hydrated provider credentials per `(userUid, agentSessionId, provider)`.

The cached state should include:

- scoped Pi agent auth directory
- provider
- credential version
- credential hash
- local hash
- hydrate timestamp
- last successful flush timestamp

Astro should rehydrate only when:

- the scoped auth directory is missing or unreadable
- the provider changes
- the credential version/hash changes
- a previous model call indicates auth failure
- the cache TTL expires, if a TTL is required for safety

Astro must not start Pi before required provider credentials are available. The improvement is
reuse and parallel preparation, not starting the model with missing auth.

### 4. Parallel preflight with dependency barriers

Astro should stop running independent preflight steps serially.

The following can be prepared concurrently when inputs are known:

- capability state lookup/materialization
- provider credential hydration/reuse
- checkpoint lease acquisition/restore where compatible

The runtime launch barrier remains strict:

- required capability paths must be ready before Pi launch or warm-turn dispatch
- required provider credentials must be ready before Pi launch or warm-turn dispatch
- checkpoint state must be safe before any turn mutates session history

This is not a fire-and-forget design. It is a parallel preparation design with explicit barriers.

### 5. Warm session runner

Astro should introduce a warm runner manager keyed by `agentSessionId`.

The warm runner owns:

- the live Pi process or equivalent long-lived runtime adapter
- the session path
- cwd/project attachment
- model/provider binding
- scoped provider auth directory
- materialized skill paths
- checkpoint lease/restore state
- a per-session turn queue

Only one turn may mutate a session at a time unless the runtime later supports safe concurrent
branches.

The warm runner should have:

- an idle TTL
- health checks
- graceful shutdown
- forced restart on incompatible state change
- cold fallback when warm dispatch fails

### 6. Pi runner protocol

Astro has verified the current Pi process contracts:

- `pi --mode json` uses print mode, which is explicitly single-shot: it sends the prompt, flushes
  stdout, and exits.
- `pi --mode rpc` is a persistent JSONL stdin/stdout protocol intended for embedding. It supports
  commands such as `prompt`, `follow_up`, `abort`, `get_state`, and model/thinking controls while
  streaming normal agent session events on stdout.

Warm process reuse must therefore target RPC mode or a dedicated runner adapter. Astro must not try
to keep the current print/json process alive.

Before implementing warm reuse, Astro must define the adapter by which a live RPC Pi process accepts
turns and maps events into the existing stream response contract.

Acceptable outcomes:

- Astro wraps Pi RPC mode with a session-bound runner manager
- Astro adds a dedicated runner adapter around Pi's internal API
- Astro adds a Pi server/worker mode for session-bound turns

Astro should not fake warm execution by keeping a process alive that cannot safely receive a second
turn.

### 7. Cold durable fallback

The current cold path remains necessary for:

- first session use
- runner crash recovery
- incompatible model/provider/cwd/capability changes
- checkpoint restore after another holder advances remote state
- deployments where warm runners are disabled

Cold fallback must be explicit and observable, not a silent performance regression.

## Invalidation Rules

A prepared runtime or warm runner must be invalidated when any of these change:

- `agentSessionId`
- `userUid`
- `agentType`
- cwd or project attachment
- model/provider binding
- reasoning effort
- session config overrides
- capability binding version/hash/count/materialized paths
- provider credential version/hash
- checkpoint state that requires restore from newer remote state
- runtime image/build revision

Auth failures must invalidate provider credential state for the affected provider.

Capability materialization failures must invalidate capability state and fail the turn rather than
running with partial skills.

## Observability Requirements

Astro must keep enough timing information to separate backend/session work, local preflight work,
Pi launch work, model output latency, and checkpoint finalization. The implemented preflight slice
currently records capability preparation duration, provider credential preparation duration, total
preflight duration, Pi spawn duration, and time from spawn to first Pi stdout line.

Required timing fields for the complete warm-runner design:

- request normalization duration
- backend session hydration duration, if used
- checkpoint preparation duration
- capability preparation duration
- provider credential preparation duration
- Pi spawn duration
- warm runner dispatch duration
- time to first model output
- total turn duration
- checkpoint finalization duration
- whether the turn used cold launch, cached preflight, warm runner, or cold fallback

Logs should identify cache hit/miss and invalidation reason.

## Rollout Plan

The rollout should be gated, measured, and reversible.

1. Add observability only. Implemented for the preflight and Pi launch phases.
2. Add cached capability preparation behind a feature flag. Implemented.
3. Add cached provider credential preparation behind a feature flag. Implemented.
4. Parallelize preflight behind a feature flag. Implemented.
5. Implement warm runner protocol behind a feature flag.
6. Enable warm runners for A2A sessions in a limited environment.
7. Promote warm runner behavior to the default only after latency, correctness, and checkpoint
   safety are verified.

## Non-goals

This ADR does not:

- move backend session allocation into Astro
- change backend-owned `AgentSession.uid` identity
- remove checkpoint durability
- remove provider credential safety
- make capability materialization fire-and-forget
- allow Pi to start without required auth
- allow Pi to start without required session-local skills
- replace `/api/a2a/chat` with a status-only endpoint
- solve generic model latency after runtime launch

## Consequences

### Benefits

- repeated same-session A2A messages can avoid the current fixed cold-start tax
- zero-capability sessions stop paying repeated no-op capability materialization cost
- provider credentials stop being rehydrated on every turn when unchanged
- warm sessions become an explicit local runtime concept instead of an accidental expectation
- cold durable execution remains available for recovery and safety

### Costs

- Astro must own a more complex local session lifecycle
- warm runners require careful invalidation rules
- checkpoint lease ownership must be coordinated with runner lifetime
- provider credential flushing and cleanup must handle longer-lived auth directories
- tests must cover both cold and warm execution paths
- Pi may need a real multi-turn runner protocol before process reuse is safe

## Implementation Tasks

- [x] Add timing instrumentation for the current `/api/a2a/chat` hot path.
- [x] Add structured logs for preflight cache hit, miss, and invalidation reason.
- [x] Add structured logs for cold fallback after warm runner dispatch exists.
- [x] Define the `PreparedSessionRuntime` data model and persistence location.
- [x] Add zero-capability preparation cache metadata.
- [x] Add capability preparation cache metadata for non-zero capability sets.
- [x] Add safe invalidation for capability preparation when binding/hash evidence changes.
- [x] Add provider credential preparation cache metadata keyed by
      `(userUid, agentSessionId, provider)`.
- [x] Add safe invalidation for provider credential preparation on local hash change or auth failure.
- [x] Add remote provider credential version/hash invalidation without waiting for cache TTL.
- [x] Refactor `runPiPrompt` so capability and provider preparation are separate operations rather
      than inline serial awaits.
- [x] Run independent preflight operations concurrently with explicit launch barriers.
- [x] Verify whether Pi supports a safe multi-turn process protocol.
- [x] Design the RPC runner adapter before process reuse.
- [x] Add a warm runner manager keyed by `agentSessionId`.
- [x] Add a per-session turn queue so only one turn mutates session state at a time.
- [x] Attach checkpoint lease lifecycle to the warm runner where safe.
- [x] Define warm runner idle TTL, health checks, graceful shutdown, and forced restart behavior.
- [x] Route `/api/a2a/chat` through warm runner dispatch when a compatible runner exists.
- [x] Fall back to cold durable launch when no compatible warm runner exists.
- [x] Add focused unit tests for zero-capability preparation cache reuse.
- [x] Add focused unit tests for non-zero capability signature cache reuse and invalidation.
- [x] Add focused unit tests for provider credential preparation cache reuse.
- [x] Add focused unit tests for provider credential cache invalidation.
- [x] Add focused tests proving second same-session preparation calls do not repeat
      zero-capability materialization.
- [x] Add focused tests proving second same-session preparation calls do not rehydrate unchanged
      provider credentials.
- [x] Add tests proving incompatible model/provider/cwd/capability changes restart or cold-fallback
      correctly.
- [x] Add tests proving concurrent same-session A2A turns are serialized or rejected safely.
- [x] Add tests proving checkpoint restore/finalization remains correct with warm runners.
- [x] Update interface/runtime docs for the implemented preflight cache environment flags.
- [x] Update interface/runtime docs after the warm-runner design is implemented.

## Related

- [`adr-25-production-a2a-discovery-and-runtime-access.md`](./adr-25-production-a2a-discovery-and-runtime-access.md)
- [`adr-27-backend-only-session-initiation.md`](./adr-27-backend-only-session-initiation.md)
- [`adr-28-durable-a2a-session-envelope.md`](./adr-28-durable-a2a-session-envelope.md)
- [`adr-32-agent-session-capability-bindings.md`](./adr-32-agent-session-capability-bindings.md)
