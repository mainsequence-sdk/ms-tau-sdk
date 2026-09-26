# ADR 0013: Guaranteed A2A Task Terminalization and Failure Observability

Status: Accepted — SDK implementation complete; managed conformance pending

Date: 2026-09-25

Amends:

- [ADR 0002: Runtime and protocol contracts](./0002-runtime-and-protocol-contracts.md) by making
  eventual Task terminalization, standard failure events, and bounded transport waiting part of
  the durable A2A contract;
- [ADR 0005: Authenticated local development mode](./0005-authenticated-local-development-mode.md)
  by distinguishing restart persistence from restart recovery and assigning local Task recovery
  to the SDK; and
- [ADR 0009: Backend model selection and Task timelines in Tau Board](./0009-tau-board-session-model-and-task-timeline.md)
  by adding first-class failure and recovery information to Task inspection.

The historical Astro
[ADR 54](../history/astro/adrs/adr-54-durable-asynchronous-a2a-task-lifecycle.md)
describes the origin of the managed dispatch design but does not govern this SDK. This record
adopts the necessary lifecycle guarantees in the current Main Sequence TAU SDK ontology.

## Context

TAU already models the standard A2A Task states. It creates a durable Task in `submitted`, claims
an execution attempt and moves it to `working`, then settles ordinary successful, failed,
canceled, rejected, or interrupted execution. Its terminal states are:

```text
completed | failed | canceled | rejected
```

`input_required` and `auth_required` are interrupted, resumable states. No separate boolean or
field named `terminalStatus` is required: terminality is derived from the Task state, as defined by
the A2A protocol.

The current implementation nevertheless does not guarantee that every created Task reaches one of
those states:

- failure settlement is best-effort and suppresses an error raised while persisting `failed`;
- backend and lease failures deliberately escape without settling the current attempt;
- process failure can happen after Task creation or attempt claim but before settlement;
- managed execution assumes that the backend dispatch system will recover an abandoned attempt,
  but that terminalization contract is not executable evidence in this repository;
- local mode persists Tasks and attempts but has no recovery owner after process restart;
- replaying an existing local Task ID returns the persisted Task without necessarily rescheduling
  a stranded `submitted` Task or reconciling a stranded `working` Task;
- synchronous Task waiting has no deadline and can wait forever for a stranded Task;
- the live streaming path can emit a custom error object after an execution failure instead of a
  standard terminal Task status update; and
- a persisted failure contains little actionable information and its current `status.message`
  representation is not guaranteed to be a complete A2A Message.

Consequently, an ordinary provider, tool, or agent error normally becomes `failed` and is visible
through Task retrieval or subscription, but failures in claim, lease, persistence, shutdown, or
recovery can leave the durable Task indefinitely in `submitted` or `working`. Persistence alone is
not recovery.

## Decision

### 1. Preserve the standard Task state model

TAU will continue to use the A2A Task state as the single lifecycle authority. It will not add a
second `terminal`, `terminalStatus`, `isFinished`, or equivalent field.

The state classes are:

| Class | States | Meaning |
| --- | --- | --- |
| Pending | `submitted` | Durable Task exists but execution has not been claimed |
| Active | `working` | A fenced attempt owns or owned execution |
| Interrupted | `input_required`, `auth_required` | Execution is deliberately paused and may continue |
| Terminal | `completed`, `failed`, `canceled`, `rejected` | No further execution occurs without a new protocol operation |

Every state transition records the transition timestamp durably. Creation time remains aggregate
metadata and the current state's time remains the standard `Task.status.timestamp`. A terminal
timestamp is the timestamp of the terminal status transition. TAU does not add non-standard
top-level timestamps to the public A2A Task.

### 2. Establish an eventual-terminalization invariant

Every successfully created Task must satisfy exactly one of these outcomes:

1. reach a terminal state;
2. reach `input_required` or `auth_required` with the information required for an authorized
   continuation; or
3. remain pending/active only while a live attempt, a bounded recovery operation, or an explicit
   execution deadline justifies that state.

A Task must not remain indefinitely in `submitted` or `working`. Each persistence implementation
must have a recovery owner, stale-state policy, bounded attempt policy, and execution deadline.
Exhausting recovery moves the Task to `failed`; it does not merely abandon the attempt.

The invariant begins only after Task creation commits. If creation fails before a Task identifier
is durably accepted, the request fails and no Task lifecycle is promised. A client disconnect or
transport wait timeout does not fail a Task whose execution is still valid.

### 3. Separate execution failure from uncertain terminalization

TAU recognizes two different outcomes:

- **execution failure**: the attempt outcome is known and TAU can persist a terminal `failed`
  transition; and
- **terminalization uncertainty**: TAU cannot prove whether the attempt or its side effects
  completed because its lease, process, or persistence path failed.

An ordinary model-provider failure, tool exception, output validation failure, output limit, or
agent execution error is an execution failure. TAU must persist `failed` before reporting a
terminal failure to the caller.

If terminal settlement itself fails, the live caller must receive a typed transport error that
states that terminal persistence is unknown. It must not receive a fabricated terminal Task. The
durable recovery owner then resolves the Task. Logs and metrics distinguish `execution_failed`
from `terminalization_unknown`.

TAU must not blindly retry an uncertain attempt. A retry is allowed only when an idempotency key,
durable checkpoint, or operation-specific contract makes replay safe. If an external side effect
may have occurred and its result cannot be proven, recovery records the attempt as `ambiguous` and
terminalizes the Task as `failed` with a safe ambiguous-outcome classification. This is a failed
Task, not a new public A2A state.

### 4. Emit a conformant, useful failure contract

For a terminal failure, `Task.status.message` is a valid A2A Message produced by the same canonical
message encoder used elsewhere. It contains an agent role, unique message identifier, and bounded
parts with a safe human-readable explanation. TAU will not place an arbitrary diagnostic mapping
directly into the wire `message` field.

Machine-readable failure information is retained in the attempt record and projected, when public
projection is appropriate, through a versioned Main Sequence metadata extension. The safe failure
shape contains at least:

- a stable failure code;
- a category such as execution, dependency, timeout, recovery, or ambiguous outcome;
- whether retry is safe according to the owning recovery policy;
- the attempt number; and
- a correlation identifier suitable for logs and support.

Raw exception strings, prompts, tool arguments/results, provider credentials, access tokens, local
paths, and traceback contents are not Task failure payloads. Internal logs may retain the existing
privacy-filtered exception type and bounded location data.

Failure status and failure explanation are committed in the same authoritative transition. A
client must never need Tau Board or local log files to determine that a Task failed.

### 5. Make streaming finish with the authoritative Task state

After a successful terminal settlement, streaming emits the standard terminal Task or Task status
update for the supported A2A protocol version, including `failed` when execution failed, marks the
update final, and closes the stream. The stream does not replace that event with TAU's current
custom `{error: {code: "task_failed"}}` success-path envelope.

If the failure could not be persisted, the stream emits one typed transport-level error identifying
terminalization uncertainty and closes. It does not set a final Task status that is absent from the
authoritative store. Subsequent Task retrieval reports the durable state, and recovery eventually
resolves it under this ADR.

Task subscription continues until a terminal state, returns interrupted states without claiming
terminality, and can replay the durable terminal event after a connection is re-established.

### 6. Bound request waiting without changing Task ownership

The server-side wait used when `returnImmediately` is false is bounded by a documented transport
wait limit. Reaching that limit returns the latest durable Task handle or the protocol's typed
non-terminal timeout result; it does not cancel or fail the Task. Polling and subscription remain
the mechanisms for observing later completion.

All waiting loops are cancellation-aware and must not outlive application shutdown. A caller's
HTTP disconnect changes only the delivery path unless the caller also issued the canonical Task
cancellation operation.

### 7. Make the SDK the local-mode recovery owner

Local mode has no Django dispatch worker, so the SDK owns reconciliation for the workspace-scoped
SQLite Task store. Reconciliation runs at startup before the process advertises Task readiness and
continues periodically while the process is serving. An idempotent replay of an existing Task ID
also triggers bounded reconciliation rather than waiting forever on a stranded record.

Local reconciliation follows these rules:

- a `submitted` Task with no claimed attempt may be scheduled from its durable initial Message and
  execution metadata;
- a `working` Task owned by the current live process remains active;
- a `working` Task whose owner is gone is marked stale and its attempt is expired;
- a stale attempt resumes or retries only from a replay-safe checkpoint or idempotent boundary;
- a stale attempt with uncertain external effects is classified ambiguous and the Task becomes
  `failed`; and
- attempt and execution limits that are exhausted make the Task `failed` with a recovery failure
  explanation.

Local claim, stale-owner detection, attempt transition, and replacement claim are transactional
and use the existing workspace/session coordination. Two local processes must not both recover the
same Task. Recovery never registers an Agent or AgentSession in Main Sequence and never uploads
local Task state.

Local persistence documentation must say that Task records survive restart and that this
reconciler, rather than SQLite alone, supplies restart recovery.

### 8. Require a complete managed recovery contract

In managed mode, the Main Sequence backend remains the durable dispatch and recovery owner. TAU is
the fenced executor; its in-process background task remains only an accelerator.

The managed contract must provide:

- transactional Task and dispatch creation;
- attempt ownership fenced by the current AgentSession execution lease;
- stale lease/attempt detection;
- safe redispatch or checkpoint resume;
- bounded attempts and execution age;
- explicit ambiguous-outcome handling; and
- atomic terminal Task transition when recovery succeeds, fails, or is exhausted.

TAU must expose the attempt, lease, heartbeat, checkpoint, and settlement operations required by
that contract, but must not create a competing managed recovery loop.

This ADR does not authorize changes to Django from this repository. Backend implementation and
deployment are work in the owning repository. This SDK's release evidence must include integration
tests against the deployed managed contract; mocked SDK tests alone cannot prove managed
terminalization.

### 9. Surface lifecycle and failure evidence in Tau Board

Tau Board remains an optional, read-only inspection surface over authoritative state. Task detail
shows:

- creation time and current/terminal status time;
- current state and whether it is pending, active, interrupted, or terminal;
- the safe failure explanation and failure code;
- attempt number and attempt outcomes;
- retry/recovery activity and the current recovery owner; and
- correlation identifiers that can be used to filter structured logs.

The board highlights Tasks older than their pending or active policy threshold. It does not infer a
failure from missing logs, mark a Task terminal, retry execution, or become part of the recovery
control plane.

### 10. Add lifecycle observability without logging sensitive content

Structured telemetry records Task creation, claim, start, interruption, settlement, settlement
failure, stale detection, retry, recovery exhaustion, ambiguous outcome, and terminal delivery.
Events carry safe Task/session/attempt/correlation identifiers and elapsed timing.

At minimum the runtime exposes counts and age distributions for Tasks in `submitted` and `working`,
settlement failures, stale attempts, retries, ambiguous outcomes, and terminal states. Managed
deployment alerting belongs to the deployment owner; local mode exposes the same evidence through
its structured log and Tau Board.

## State and Recovery Matrix

| Failure window | Authoritative behavior |
| --- | --- |
| Before durable Task creation | Request fails; no Task exists |
| After creation, before claim | Task remains briefly `submitted`; recovery owner schedules it or terminalizes recovery failure |
| During ordinary provider/tool/agent execution | Attempt and Task settle as `failed` with a conformant status Message |
| During artifact persistence | Retry only if the append/finalize operation is idempotent; otherwise reconcile as ambiguous |
| During terminal settlement | Caller receives terminalization-unknown transport error; recovery resolves durable state |
| Lease loss | Old executor stops writing; recovery validates replay safety and redispatches or fails ambiguous |
| Process crash or forced shutdown | Recovery expires the old attempt and resumes safely or terminalizes failure |
| Client disconnect or wait timeout | Task continues under its recovery owner; no implicit cancellation |
| Explicit Task cancellation | Attempt stops at a safe boundary and Task becomes `canceled` |
| Required input or authorization | Task enters the corresponding interrupted state and awaits continuation |

## Rejected Alternatives

### Add a second terminal-status field

Rejected. It duplicates the standard state machine and creates contradictory combinations such as
`working` plus `terminal=true`.

### Keep failure settlement best-effort

Rejected. Suppressing settlement errors without durable recovery makes a failed execution appear
active forever.

### Treat persisted local rows as restart recovery

Rejected. Durable state records what happened; it does not claim, resume, retry, or terminalize
abandoned work.

### Replay every stale working Task

Rejected. Agent tools can perform irreversible external side effects. At-least-once execution
without an idempotency or checkpoint contract can duplicate those effects.

### Fail a Task when an HTTP caller disconnects or times out

Rejected. Transport lifetime and Task lifetime are separate. A durable Task may legitimately
outlive its initiating request.

### Use Tau Board or log inspection as the failure authority

Rejected. Clients must be able to determine failure from the A2A Task aggregate. Board and logs are
diagnostic projections only.

### Implement managed recovery inside TAU

Rejected. It would create a second dispatcher competing with the Main Sequence backend and weaken
the existing lease and attempt ownership boundary.

## Consequences

### Positive

- Clients can rely on the standard Task state instead of a TAU-specific terminal flag.
- Ordinary execution failure is visible consistently through send, stream, get, and subscribe.
- Local Tasks no longer remain stranded merely because the local process restarted.
- Managed terminalization becomes an explicit, testable cross-repository contract.
- Ambiguous external side effects are not silently executed twice.
- Tau Board can explain failures without becoming the source of truth.

### Negative

- Local mode requires a reconciler, ownership expiry, retry policy, and schema evolution for attempt
  outcomes.
- Some crashed Tasks will safely fail as ambiguous instead of being automatically retried.
- Managed conformance requires coordinated evidence from a backend owned outside this repository.
- Failure metadata and standard stream events require a hard wire-contract change from the current
  custom streaming error envelope.

## Delivery Phases and Gates

### T0: Contract baseline and failure inventory

- Freeze the supported A2A Task, TaskStatus, Message, and streaming-event shapes in fixtures.
- Inventory every failure boundary from create through claim, execution, artifact output,
  settlement, delivery, shutdown, and restart.
- Define stable failure codes, safe public fields, internal attempt outcomes, retryability, stale
  thresholds, attempt limits, and execution deadlines.
- Record the managed backend operations and evidence needed by the SDK without modifying Django.

Gate: every failure boundary has exactly one recovery owner and expected durable state; contract
tests reject a second terminal flag and reject a non-Message `Task.status.message`.

### T1: Standard terminal failure and bounded delivery

- Make failure settlement return the authoritative failed Task.
- Emit the standard terminal failed Task/status event and remove the custom successful-stream error
  substitute.
- Distinguish execution failure from terminalization uncertainty.
- Bound synchronous Task waiting without canceling the Task.

Gate: REST and JSON-RPC send, stream, get, and subscribe expose the same durable terminal state;
provider, tool, validation, and output failures all end in `failed`; a settlement outage never
fabricates a terminal Task; and no wait loop is unbounded.

### T2: Local reconciliation and safe restart behavior

- Add local stale-owner and attempt outcome persistence.
- Reconcile `submitted` and `working` Tasks at startup, periodically, and on idempotent replay.
- Resume only checkpointed/idempotent work and terminalize uncertain work as failed/ambiguous.
- Prevent duplicate recovery across concurrent local processes.

Gate: failure-injection tests kill the process before claim, after claim, during output, and during
settlement; after restart every Task resumes safely, becomes interrupted/terminal, or remains
active only under a proven live owner. No test produces duplicate non-idempotent tool execution.

### T3: Managed recovery conformance

- Publish the exact SDK/backend recovery contract and test fixtures.
- Verify lease expiry, stale attempt handling, bounded redispatch, exhausted recovery, ambiguous
  outcome handling, and terminal event replay against the deployed backend.
- Add SDK integration tests for backend unavailability and lease loss.

Gate: a managed Task abandoned at each failure boundary eventually becomes terminal or explicitly
interrupted, and no stale executor can write after ownership changes. Completion of this phase
requires evidence from the owning backend repository; it does not authorize backend changes here.

### T4: Board and operational observability

- Add failure, attempt, recovery, timestamp, and correlation views to Tau Board.
- Add structured lifecycle telemetry and stale-state diagnostics.
- Preserve the board's read-only boundary and existing asset budgets.

Gate: the board explains a failed, ambiguous, interrupted, retrying, and completed Task entirely
from authoritative state plus safe telemetry, while tests prove that opening the board cannot
change Task state.

### T5: Documentation and release verification

- Update the README, runtime contract, local-development skill, A2A adapter skill,
  troubleshooting guide, public API, settings reference, testing guide, and changelog.
- Document terminal versus interrupted states, transport timeout behavior, retry safety, local
  restart recovery, and managed recovery ownership.
- Exercise clean-install local and managed Task scenarios.

Gate: packaged documentation and skills describe the implemented contract exactly; the full unit,
contract, board, and end-to-end suites pass; distribution verification contains no stale custom
streaming-failure examples or claims that persistence alone guarantees recovery.

## Implementation Ledger

| Phase | Status | Evidence required |
| --- | --- | --- |
| T0 | Complete | Protocol helpers, failure inventory, recovery-owner matrix, and contract tests |
| T1 | Complete | Cross-transport terminal failure, uncertainty, valid-Message, and bounded-wait tests |
| T2 | Complete | Durable local recovery metadata, startup/periodic reconciliation, lease-owner protection, exhaustion, and ambiguous-outcome tests |
| T3 | Pending external conformance | Deployed managed-backend lease-loss, stale-attempt, bounded-redispatch, and terminal-event evidence; no Django change is authorized by this ADR |
| T4 | Complete | Board failure/recovery inspection, health diagnostics, structured lifecycle telemetry, and read-only Board tests |
| T5 | Complete for SDK-owned gates | Documentation and skills updated; core, Board, lint, type, JavaScript, distribution, and isolated clean-install gates pass. Managed end-to-end evidence remains part of T3 |

The SDK implementation deliberately does not claim that T3 is complete. Managed-mode lifecycle
recovery is owned by the backend and must be demonstrated against the deployed implementation.
The SDK now publishes the wire contract and handles managed recovery results, but this repository
does not modify or emulate that backend.

## Acceptance Criteria

This ADR is complete only when:

1. no separate terminal-status field exists and all clients derive terminality from Task state;
2. every created local Task eventually becomes terminal or explicitly interrupted, including
   across restart;
3. managed integration tests prove the same invariant under lease loss and worker failure;
4. a known execution failure durably becomes `failed` with a valid A2A status Message;
5. a settlement failure is reported as terminalization uncertainty and later reconciled;
6. streaming ends with the same authoritative terminal state returned by get/subscribe;
7. synchronous request waiting is bounded and timeout does not cancel the durable Task;
8. uncertain non-idempotent side effects are not automatically replayed;
9. Tau Board shows safe failure and recovery evidence without owning lifecycle state; and
10. failure-injection tests cover create, claim, start, provider/tool execution, artifact output,
    settlement, lease loss, disconnect, shutdown, crash, and restart.

## References

- [A2A protocol buffers](https://github.com/a2aproject/A2A/blob/main/specification/a2a.proto)
- [A2A protocol specification](https://github.com/a2aproject/A2A/blob/main/docs/specification.md)
