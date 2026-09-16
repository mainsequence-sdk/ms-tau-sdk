# ADR 54: Durable Asynchronous A2A Task Lifecycle

Status: Accepted

Date: 2026-09-15

Implementation Status: Astro/Tau implementation complete; coordinated Django deployment and
end-to-end conformance remain required before Task mode is advertised. The AgentTask/Job separation
is a coordinated hard cut with no legacy compatibility path.

Coordinates With: `tdag-django` Agents ADR 034, Durable Asynchronous A2A Task Control Plane

Amends:

- ADR 47 by adding explicit Task selection while preserving its response-kind v1 default of
  `message`;
- ADR 47 by accepting standard `returnImmediately`; and
- ADR 47 by separating Task result selection from Task waiting behavior.

Reaffirms:

- ADR 46: external A2A push notifications remain disabled.

## Context

Astro exposes a durable A2A Task representation through Django but does not yet provide durable
Task execution. The Task send path creates the backend row and starts execution with
`RuntimeManager.create_background_task`, which delegates to `asyncio.create_task`. Shutdown drains
these tasks for a bounded period and then cancels them. A Task can therefore survive while its only
executor disappears.

Other lifecycle gaps are coupled to that problem:

- controlled outbound A2A always selects a direct Message;
- the custom response-kind v1 contract supports only direct Message in current controlled clients;
- the public endpoint rejects the standard `returnImmediately` setting;
- `input_required` and `auth_required` are serializable but cannot be deliberately entered and
  resumed by this execution path;
- REST subscription polls Task snapshots while JSON-RPC SubscribeToTask is not routed;
- detached work does not publish replayable incremental artifact updates; and
- returning a Task to Agent A does not arrange for Agent A to run again when Agent B becomes
  terminal or needs input/authorization.

The A2A standard permits Message or Task results, uses `returnImmediately` as a Task waiting
preference, defines interrupted Task states and continuation Messages, and defines polling,
subscription, and optional push notification mechanisms. Main Sequence additionally needs a
deterministic result-shape contract for its controlled MCP, SDK, and CLI clients.

## Terminology

In this record, **Astro** means the deployed `astro-tau` runtime service/process that hosts the
target AgentSession and executes the agent turn. It does not mean Django, Celery, or the calling
Agent A.

**ADR-027 wake** means the existing Django-owned scale-from-zero operation. It sends `GET /ready` so
Knative starts an Astro runtime and Django can confirm that it is serving. That wake does not carry
an A2A Task and does not authorize Task execution.

The 2026-09-15 follow-up explicitly accepts reuse of this wake technology. A2A dispatch reuses
ADR-027's durable operation, Celery execution, readiness observation, retries, and signed runtime
credential boundary; it must not introduce a second runtime-wake subsystem.

**Pull and claim** means that, after a signed `dispatch available` signal, Astro calls Django to
load and atomically claim the referenced durable dispatch. It is an on-demand claim, not continuous
runtime polling and not execution based only on a UID received from the signal.

## Decision

Astro becomes an executor under the canonical runtime authorization and AgentSession lease, and an
A2A protocol adapter over the durable Django Task control plane defined by coordinated `tdag-django`
Agents ADR 034.

### 1. Separate result shape from Task waiting

Main Sequence retains the existing response-kind v1 extension whose field selects the successful
result shape:

```json
{
  "configuration": {
    "responseKind": "message"
  }
}
```

The canonical extension remains:

```text
https://mainsequence.ai/a2a/extensions/response-kind/v1
```

Its `defaultResponseKind: "message"` remains authoritative on the public A2A wire contract.
Omitting `responseKind` there deterministically selects Message for arbitrary external clients. An
explicit `responseKind: "task"` selects Task. Discovery adds `task` to the v1
`supportedResponseKinds` only after the coordinated implementation passes conformance; no v2
extension is introduced.

MCP, SDK, and CLI are stricter controlled entry points: each requires the caller to provide
`response_kind` explicitly and has no local default. They always serialize the selected value onto
the wire. This preserves public v1 compatibility without allowing Main Sequence-controlled callers
to choose a result shape accidentally.

The extension remains optional on the public protocol surface. An arbitrary external A2A client may
omit it and receives the compatible direct Message behavior. Task creation requires an explicit
selection rather than server inference.

Astro accepts standard `configuration.returnImmediately` on public SendMessage requests:

- when the explicitly selected result is Task, `true` returns the initial Task promptly;
- when the result is Task, `false` or omission waits until a terminal or interrupted state and
  returns that Task; and
- when the result is a direct Message, the field has no effect.

The two fields are not aliases. `responseKind` selects Message versus Task; `returnImmediately`
controls waiting only for a Task. Streaming continues to use its own operation contract and does
not accept the response-kind extension.

### 2. Consume the expanded MCP Task contract

The Django-owned canonical MCP surface is:

```text
a2a.send_message
a2a.get_task
a2a.cancel_task
a2a.wait_task
```

Astro projects these tools through its generic MCP adapter and injects the existing trusted caller
session proof. It must not reintroduce a competing local sender.

`a2a.send_message` requires `response_kind: "message" | "task"`; omission fails MCP schema
validation before target resolution or network activity. An explicit Task send also requires
`completion_policy: "poll" | "resume_caller"`:

- `poll` returns the Task handle with no automatic caller continuation;
- `resume_caller` returns the handle and registers a durable caller delivery.

`completion_policy` is invalid for Message mode because there is no Task lifecycle to complete.

For controlled Task sends the MCP sender uses explicit `responseKind: "task"` and
`returnImmediately: true`. `a2a.wait_task` supplies bounded event-cursor waiting after the initial
return. The MCP result is a strict discriminated Message-or-Task union; Astro must preserve it
without converting an empty or unexpected response.

A continuation Message identifies the authorized Task. The backend derives the target Agent and
session; model-authored input cannot attach a Task to a different session.

### 3. Execute only from a durable dispatch under the canonical session lease

Before Astro acknowledges asynchronous Task creation, Django must durably commit the Task, initial
Message, creation events, and an executable dispatch/outbox record in one transaction.

Astro may begin work only after it acquires the Task AgentSession's existing `runtime_run`
`AgentSessionLease` and claims the dispatch's current `AgentTaskExecutionAttempt`. Every executor
write uses the existing canonical runtime credential and proves the current session lease while also
carrying the current attempt UID as a database state fence. The backend derives the monotonic attempt
number; Astro does not present it as proof.

Astro never uses generic Task status, output, or agent/system Message mutation operations for a
dispatch-backed Task. Executor writes use only the canonical runtime-authorized, attempt-aware
services. User continuation Messages use only the authorized continuation service.

This ADR does not add an attempt lease, attempt bearer capability, attempt secret, Task-specific
heartbeat, runtime authentication class, principal, scope, header, credential, or wake token. The
AgentSession lease is the execution lease. The attempt identity is not authorization.

An in-process `asyncio.Task` is allowed only as an optimization after the durable dispatch and claim
exist. It is never the recovery owner. Dispatch uses three separate steps:

1. Django/Celery uses the ADR-027 backend-owned wake operation to make sure the target runtime is
   serving. ADR-027's `GET /ready` request only wakes/checks the service; it does not carry or
   authorize a Task.
2. Once the runtime is serving, Django sends a bounded signed internal `dispatch available` signal
   through the existing runtime RPC credential boundary. Task and dispatch UIDs are selectors, not
   bearer capabilities. This is a Task-control signal separate from ADR-027's `/ready` operation.
3. Astro uses the selectors to pull and atomically claim the durable dispatch from Django. Only the
   authorized database claim plus the active AgentSession lease permits execution. This is an
   on-demand pull triggered by the signal, not continuous polling.

The same signal is sent when the runtime is already serving, so dispatch does not depend on a cold
start. Signal failure is retried from the durable dispatch row, and a duplicate signal is harmless.
ADR-027 remains the single runtime-wake technology; only the post-wake signal and durable Task claim
are A2A-specific.
If the canonical AgentSession lease expires or changes holder, recovery creates another attempt only
when an idempotency or checkpoint contract makes replay safe. Otherwise the attempt becomes
ambiguous/failed for reconciliation.

Shutdown no longer converts an otherwise valid A2A Task to canceled merely because one Astro
process is exiting. Astro stops claiming new work, checkpoints/releases when possible, and lets the
existing AgentSession lease/recovery contract handle an interrupted process.

Delivery is at least once. Astro does not claim exactly-once execution. If a process disappears
after an external side effect whose outcome cannot be determined, recovery must not blindly repeat
that effect. The attempt is settled with an explicit ambiguous/failure classification unless the
agent operation provides an idempotency key or resumable checkpoint.

### 4. Keep the AgentTask aggregate completely independent from Pod Manager

Hard-cut invariant: the AgentTask aggregate contains no `job`, `job_run`, `job_run_output`,
`artifact`, `JOB_RUN_LINKED`, Job status mapping, Job creation/start flags, Job cancellation cascade,
or other typed relation to or behavioral dependency on Pod Manager.

This hard cut is explicitly accepted by the 2026-09-15 follow-up decision. It supersedes the earlier
proposal to retain optional JobRun support for genuinely job-backed Tasks. Implementation removes
the typed relationships and purges pre-cutover AgentTask aggregate rows during the coordinated
maintenance migration; it does not delete the independent Pod Manager Job, JobRun, JobRunOutput, or
Artifact rows.

An A2A Task is the Agents-domain system of record for agent communication and its durable protocol
lifecycle. It is not an execution wrapper or Pod Manager projection. Every Task requires an
AgentSession and durable dispatch. Astro must not create or maintain any typed AgentTask relation to
`BaseJob`, `JobRun`, `JobRunOutput`, or `Artifact`, mirror JobRun status into Task status, or let
JobRun cancellation drive Task cancellation.

While executing a Task, an agent may invoke the canonical Pod Manager tools to start zero, one, or
many jobs as side effects. Each JobRun retains its own authorization, retry, status, output, and
cancellation semantics. The agent observes those results and decides how to settle its Task. Task
cancellation does not implicitly cancel jobs; the agent may request child-job cancellation through
the normal Pod Manager contract when appropriate.

If the Task needs the later job result, Astro treats the returned JobRun UID as ordinary tool state
and may checkpoint/poll it through the existing AgentSession machinery. A future event-driven
external-operation wake must be generic across tools and may only resume the agent; it must not
project JobRun state into Task state. That generic wake is outside this ADR.

The coordinated Django cutover removes all pre-cutover AgentTask aggregate rows and every Pod
Manager-specific Task foreign key, event, serializer/filter field, creation flag, status projection,
cancellation path, and lifecycle dependency. The underlying Pod Manager Job, JobRun, JobRunOutput,
and Artifact rows remain independent and are not deleted. No replacement Task-to-Job foreign key,
output link, wrapper, status bridge, legacy branch, or second execution mode is introduced.

### 5. Implement interruption and continuation

The Task execution environment exposes trusted structured controls equivalent to:

```text
task.request_input(reason, schema, prompt)
task.request_authorization(requirement_reference, prompt)
```

Astro injects the active Task and attempt identity; the model cannot select another Task. Invoking a
control persists the structured status Message and event, transitions the Task to
`input_required` or `auth_required`, and settles the attempt as interrupted. The runtime releases the
canonical AgentSession lease through its normal session lifecycle; there is no Task-specific release
protocol.

Credentials, bearer tokens, and provider secrets never appear in A2A parts or status Messages.
`auth_required` carries a safe backend requirement reference and human-readable next action. The
existing backend-owned provider authorization attempt/requirement flow obtains or refreshes the
credential. A later continuation Message references the satisfied requirement without copying the
secret. This ADR does not add a generic parallel authorization-requirement registry.

Continuation appends an idempotent Message to the same Task/context and creates a new durable
dispatch. The lifecycle is:

```text
submitted -> working | rejected | failed | canceled
working -> input_required | auth_required | completed | failed | rejected | canceled
input_required -> submitted | canceled | failed
auth_required -> submitted | canceled | failed
terminal -> no transition
```

Only an accepted continuation moves an interrupted Task back to submitted. Claiming its dispatch
moves it to working.

### 6. Use one replayable Task event stream

Astro replaces snapshot-only subscription polling with a shared `TaskEventStream` adapter backed by
the Django event cursor.

Before opening either protocol subscription, Astro checks the authorized snapshot. If the Task is
already terminal, it returns the A2A `UnsupportedOperationError`; clients retrieve the final Task
with GetTask. For a nonterminal Task, both REST Task subscription and JSON-RPC SubscribeToTask use
the same sequence:

1. obtains an authorized atomic Task snapshot and event watermark;
2. emits the current Task first;
3. reads events strictly after the watermark;
4. maps status and output deltas to A2A Task status/artifact update events;
5. advances only after an event is serialized; and
6. closes at terminal state.

Redis or another backend wake hint may reduce latency, but the ordered Django event log is the
replay source. Losing a hint cannot lose an event; bounded polling recovers it. Multiple subscribers
may read the same Task independently. Reconnect uses the last durable event cursor.

Task authorization is revalidated from backend ownership and delegated-session rules. Possession of
a protocol Task ID is not authorization.

### 7. Persist coalesced artifact deltas

During Task execution Astro writes output through backend create, append, and finalize operations.
Each operation atomically updates the current output projection and appends an immutable ordered
event containing the A2A artifact delta, including artifact ID, `append`, `lastChunk`, parts, and
safe metadata.

These are A2A protocol artifacts, not Pod Manager Artifact or JobRunOutput records. Astro serializes
validated protocol Parts and never asks Django to create a typed Pod Manager relationship for Task
output.

Astro coalesces streaming model output by configurable time/size thresholds. It must not write one
PostgreSQL row per token. Operational defaults may start in the range of 100-250 ms or 4-16 KiB and
may be tuned without changing this ADR.

Strict JSON output is buffered and validated. It produces one final artifact; invalid partial JSON
is never published as a durable update.

The direct `message:stream` path may continue emitting request-local deltas, but when it represents
a Task it must persist the same durable artifact events so a later subscriber can replay them.

### 8. Resume Agent A through an internal durable delivery

`resume_caller` creates a delegation bound to the caller AgentSession proven by trusted MCP metadata.
Astro never accepts a model-supplied caller session.

When the target Task reaches a terminal or actionable interrupted state, Django creates an
idempotent caller-delivery record and wakes the caller runtime through the internal runtime control
path. Astro claims the caller continuation under the caller session's existing `runtime_run` lease
discipline and inserts a platform-authored event containing:

- Task UID and protocol Task ID;
- state;
- triggering event cursor.

The wake payload is bounded. Agent A calls `a2a.get_task` or `a2a.wait_task` for complete output. If
the caller session already has an active turn, the delivery remains queued; Astro must not run two
turns concurrently for the same session. Retries are idempotent by delegation and triggering event
sequence.

Caller delivery introduces no separate Astro/Django lease or authentication mechanism. Durable
workers claim delivery rows transactionally, and actual caller execution is fenced by the canonical
AgentSession lease and existing caller-session proof.

### 9. Keep external push notifications disabled

ADR 46 remains in force. Astro advertises `pushNotifications: false`, rejects push configuration
operations with the protocol-defined unsupported error, and performs no external webhook delivery.

The internal dispatch/event/caller-wake mechanisms are platform control-plane operations, not A2A
push notification configs and not user-provided webhooks. Re-enabling public push requires a
separate ADR and the security/delivery prerequisites listed by ADR 46.

## Ownership Boundary

| Component | Responsibility |
| --- | --- |
| Django Agents domain | Durable Task, messages, events, outputs, dispatch, attempts, cancellation, delegation, caller delivery |
| Django MCP | Compatible result selection, Task-only completion policy, caller proof, target access, wire send, discriminated result, Task tools |
| Django Celery workers | Dispatch/wake/recovery hints and retry of durable pending operations |
| Astro A2A API | Public protocol validation/serialization, REST and JSON-RPC streaming adapters |
| Runtime authorization | Existing runtime credential plus Task AgentSession's canonical `runtime_run` lease |
| Astro runtime | Acquire the session lease; claim, execute, checkpoint, publish output, interrupt, settle, resume caller |
| Redis | Optional low-latency wake hint only |

## Cancellation

Cancellation first persists intent. If the Task has no active attempt, Django may settle it canceled
immediately. If an attempt is active, the internal dispatcher wakes the executor and the executor
acknowledges cancellation and settles the Task. If the executor disappears, canonical
AgentSession-lease recovery observes cancellation intent and settles without starting new work.

Astro checks cancellation at bounded intervals and before every output/status mutation. A terminal
Task remains immutable and cancellation is idempotent. Task cancellation never directly changes a
JobRun; child-job cancellation, when wanted, uses the independent Pod Manager contract.

## Hard Cutover And Rollout

1. Keep Task undiscoverable and stop Astro Task writers for the maintenance window.
2. Take the normal pre-migration database backup.
3. Apply the coordinated Django hard-cut migration: purge pre-cutover AgentTask aggregate rows,
   remove the entire Job-specific Task surface, require AgentSession, and add the durable control-plane
   records.
4. Deploy Django and Astro together. Astro uses only the new dispatch/attempt services under canonical
   runtime authorization and the existing AgentSession lease; mixed old/new binaries are unsupported.
5. Add output delta persistence, the shared subscription adapter, JSON-RPC SubscribeToTask,
   structured interruption, and Task continuation.
6. Update MCP, SDK, CLI, and the platform A2A skill in the same release so controlled callers must
   provide `response_kind`, while public A2A wire omission preserves v1 Message behavior. Require
   `completion_policy` only for explicit Task mode.
7. Update the canonical Agents documentation, platform ontology, DRF schema, affected static-resource
   hashes, and focused Agents/MCP tests so no published contract retains an AgentTask/Pod Manager
   relationship.
8. Add caller delegation/delivery and verify `resume_caller` under active-turn and restart races.
9. Add Task to response-kind v1 discovery only after the end-to-end conformance suite passes.

Because the hard cut removes columns and deletes pre-cutover Task rows, rollback requires the
pre-migration database backup plus the former Django and Astro versions. Otherwise recovery is by
forward fix. A runtime must never advertise a lifecycle that the deployed control plane cannot
recover.

## Rejected Alternatives

- A separate Astro Task credential or attempt lease would duplicate Django's canonical runtime
  authorization and AgentSession lease and could produce conflicting ownership decisions.
- Mutually exclusive runtime-dispatched and Job-backed Task modes would still preserve two Task
  lifecycle implementations and would not represent a Task that invokes several jobs.
- Response-kind v2 is unnecessary: controlled MCP/SDK/CLI entry points can require an explicit
  choice locally while the public v1 wire contract retains its compatible Message default.

## Consequences

- A persisted Task remains executable/recoverable across Astro restarts and replicas.
- Controlled callers make result and completion behavior explicit.
- Public A2A clients are not forced to understand a Main Sequence extension.
- Interrupted Tasks and detached artifact streams become resumable and replayable.
- Agent A can continue autonomously after Agent B reaches an actionable state.
- Agent Tasks retain one lifecycle authority while agents may invoke any number of independent jobs.
- The design adds database records, recovery workers, state-machine validation, and
  operational monitoring.
- The hard cut purges pre-cutover AgentTask rows and requires a maintenance window; mixed versions
  and a simple binary rollback are unsupported.
- At-least-once execution requires idempotent tools/checkpoints for safe automatic retry.
- External push interoperability remains unavailable by deliberate choice.

## Non-Goals

This ADR does not implement external webhook push notifications, claim exactly-once execution,
store provider credentials in A2A data, link AgentTask to JobRun as execution/status/cancellation
authority, add a second runtime authentication or lease system, or make Redis/Channels the event
source of truth.

## Acceptance Criteria

- MCP, SDK, and CLI reject omitted `response_kind` before network activity; the public A2A wire
  contract alone preserves omission as a deterministic Message request.
- Public SendMessage accepts standard `returnImmediately` and does not require the extension.
- An explicit Message request returns only Message; an explicit Task request returns only Task.
- Only explicit Task mode requires `completion_policy`.
- No Task is acknowledged before its dispatch is durable.
- A killed Astro process is recovered after the canonical AgentSession lease expires without
  accepting stale-attempt writes.
- Executor writes require canonical runtime authorization, the Task session's active lease, and the
  current attempt fence; there is no Task-specific credential or lease.
- No Task aggregate record has any Pod Manager relation, and independent JobRun lifecycle changes do
  not mutate the Task.
- Every Task has the AgentSession and dispatch used for canonical authorization, leasing, and
  routing.
- The AgentTask surface contains no Pod Manager foreign key, event, request/response/filter field,
  creation flag, status mapping, cancellation cascade, or Task-specific Pod Manager dependency, and
  no pre-cutover AgentTask row survives the migration.
- Graceful Astro shutdown does not cancel a Task solely because the process exits.
- The lifecycle service rejects invalid transitions and terminal mutation.
- Input/auth controls produce structured interrupted states and continuation creates a new attempt.
- REST and JSON-RPC subscribers see the same ordered replayable status/artifact events.
- SubscribeToTask on an already terminal Task returns `UnsupportedOperationError`.
- Incremental output survives reconnect/restart, and token-level writes are coalesced.
- Strict JSON exposes only a valid final artifact.
- `poll` never creates a caller wake; `resume_caller` durably and idempotently wakes the proven
  caller on terminal/input/auth states.
- Cancellation is idempotent before claim, during execution, and after executor loss.
- Agent Cards continue advertising push notifications as unsupported.
- Task support remains undiscoverable until all required end-to-end tests pass.

## References

- A2A v1.0 specification: https://a2a-protocol.org/v1.0.0/specification/
- A2A streaming and asynchronous operations:
  https://a2a-protocol.org/latest/topics/streaming-and-async/
- A2A extensions: https://a2a-protocol.org/latest/topics/extensions/
- ADR 37: A2A Standard Wire Protocol
- ADR 43: Backend-Backed A2A Task Persistence
- ADR 46: Disable A2A Push Notifications Until Backend Support Exists
- ADR 47: Explicit A2A Response Kind
- `tdag-django` Agents ADR 027: Runtime Wake Signal And Presence For Scale-From-Zero Coding-Agent
  Runtimes
- `tdag-django` Agents ADR 034: Durable Asynchronous A2A Task Control Plane
