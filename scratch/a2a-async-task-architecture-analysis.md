# Asynchronous A2A Task Architecture: Analysis And Accepted Resolution

Date: 2026-09-15

Status: Accepted architecture; implementation not started by this document

Coordinated decisions:

- Astro Tau: [ADR 54](../docs/adrs/adr-54-durable-asynchronous-a2a-task-lifecycle.md)
- `tdag-django`: `docs/agents/adr/adr-034-durable-asynchronous-a2a-task-control-plane.md`

## Executive Summary

Main Sequence currently persists A2A Task snapshots in Django, but an asynchronous Tau execution is
still owned by one Astro process through `asyncio.create_task`. The outbound MCP tool also forces a
direct Message result, task interruption states are not resumable, JSON-RPC task subscription is
missing, and detached work does not publish durable incremental artifact events.

The accepted resolution is:

1. Controlled Main Sequence callers must choose `response_kind` explicitly. There is no SDK, CLI,
   or MCP default.
2. Main Sequence keeps a versioned response-kind extension for deterministic Message-versus-Task
   results, while the public A2A endpoint also accepts the standard `returnImmediately` field and
   does not require the extension from arbitrary external clients.
3. MCP exposes Task lifecycle tools: `a2a.get_task`, `a2a.cancel_task`, and bounded
   `a2a.wait_task`. A Task send also requires `completion_policy: "poll" | "resume_caller"`.
4. External webhook push notifications remain disabled. Main Sequence uses its internal durable
   event and wake path for agent-to-agent coordination.
5. Django is the durable Task, dispatch, execution-attempt, event, output, and caller-delivery
   authority. Celery is a dispatcher and wake mechanism. Astro executes work only after claiming a
   fenced lease from Django.
6. `input_required` and `auth_required` become real paused states with structured reasons and a
   continuation path. Provider credentials remain out of A2A content.
7. REST and JSON-RPC subscriptions share one replayable event stream.
8. Live artifact updates are persisted in bounded chunks. Token-by-token database writes are not
   allowed; strict JSON output remains buffered until one valid final artifact exists.
9. `resume_caller` durably wakes Agent A when Agent B reaches a terminal or actionable interrupted
   state.

The intended flow is:

```text
Agent A
  |
  | a2a.send_message(response_kind="task",
  |                  completion_policy="resume_caller")
  v
Django MCP sender --------------------------+
  |                                         |
  | direct authenticated A2A message:send   | durable caller/task link
  v                                         v
Target Astro -> Django transaction: Task + initial Message + Dispatch + events
  |                                         |
  |<-------------- Task handle -------------+
  |
Django/Celery wake -> target Astro claims fenced execution lease
  |
Target Astro executes and persists status/artifact events
  |
  +-> REST SSE / JSON-RPC subscription reads the durable event cursor
  |
  +-> internal caller delivery wakes Agent A on terminal/input/auth state
```

## Current Implementation And Gaps

### Outbound result selection

The Django MCP sender currently constructs a wire request with
`configuration.responseKind = "message"`, and its schema/parser only accepts a Message response.
Astro's public parser defaults an omitted custom field to Message and rejects
`configuration.returnImmediately`.

This prevents a model using the canonical constrained tool from creating and following an
asynchronous Task. It also conflates two separate questions:

- `responseKind` is a Main Sequence extension that selects the successful result shape.
- `returnImmediately` is the standard A2A Task waiting preference. It does not select Message
  versus Task and has no effect when the server returns a direct Message.

### Durable state versus durable execution

`AgentTask`, messages, outputs, and ordered events exist in Django. However, detached Tau work is
started with `asyncio.create_task` in Astro. A process exit can therefore stop execution even though
the Task row survives. Graceful shutdown drains briefly and can cancel remaining work; there is no
durable dispatch record, claim lease, heartbeat, fencing token, or recovery sweep.

### Interrupted tasks

The model includes `input_required` and `auth_required`, but the execution path has no structured
way for an agent to enter those states, stop safely, and resume after a new Message. The backend
status service also protects terminal states only; it does not enforce a complete lifecycle state
machine.

### Subscriptions and live output

The REST Task subscription polls whole backend snapshots. JSON-RPC does not route SubscribeToTask.
The live streaming path can expose updates during its own request, but a detached Task does not
persist and replay incremental artifact deltas. The existing output model is readable but lacks a
complete create/append/finalize service contract.

### Caller resumption

Returning a Task handle is not enough for agent-to-agent autonomy. Agent A has no durable
subscription saying that its session should run again when Agent B completes or asks for input or
authorization.

## Accepted Contract Decisions

### 1. Required `response_kind` for controlled clients

`response_kind` is required in all controlled Main Sequence entry points:

- MCP `a2a.send_message`;
- the Python SDK helper and builder; and
- the CLI flag.

There is no function, schema, or CLI default. Omitting it is a local validation error before a
network call. The result is a discriminated union:

```text
response_kind="message" -> Message result
response_kind="task"    -> Task result
```

The current response-kind extension v1 retains its public wire default of Message. Controlled MCP,
SDK, and CLI entry points are intentionally stricter: they require an explicit argument and always
send it with the v1 activation header. This avoids a v2 extension while still preventing hidden
defaults in the interfaces Main Sequence controls.

The public A2A endpoint remains usable by ordinary standards-compliant clients:

- an external client may omit the custom extension entirely;
- `returnImmediately` is accepted as the standard Task wait preference;
- when the server chooses a Task result, `true` returns the initial Task promptly and `false` or
  omission waits until terminal or interrupted state before returning the Task; and
- when the server chooses a direct Message, `returnImmediately` has no effect.

Initially, the no-extension path preserves direct Message behavior unless a task-native runtime
operation explicitly chooses a Task. This is server behavior, not a hidden default for the custom
field.

For controlled Task sends, the sender uses explicit `responseKind: "task"` plus
`returnImmediately: true`; later waiting is governed by the completion policy.

### 2. MCP Task lifecycle

The canonical tool surface becomes:

```text
a2a.send_message
a2a.get_task
a2a.cancel_task
a2a.wait_task
```

`a2a.send_message` requires `response_kind`. When it is `task`, it also requires:

```json
{"completion_policy": "poll"}
```

or:

```json
{"completion_policy": "resume_caller"}
```

`poll` returns a durable handle and creates no automatic caller continuation. The agent may use
`get_task`, bounded `wait_task`, or `cancel_task`. `wait_task` waits on the event cursor up to a
server-defined maximum and returns the latest state plus cursor if still non-terminal; it never
blocks an MCP worker indefinitely.

`resume_caller` returns the same handle and additionally creates a durable delegation/delivery
record tied to the verified caller session. It wakes that session on:

- `completed`, `failed`, `canceled`, or `rejected`;
- `input_required`; or
- `auth_required`.

Continuation of an interrupted Task uses the canonical sender with the Task UID as a selector. The
backend derives the target Agent and session from the authorized Task; the model cannot combine a
Task with a different target session.

### 3. No external push notifications in this phase

A2A push notification configuration and webhook delivery are optional standard capabilities. They
remain explicitly unsupported and Agent Cards continue to advertise
`pushNotifications: false`.

Internal Main Sequence coordination does not use public webhooks. It uses the Task event log,
durable dispatch/outbox rows, Celery wake signals, and authenticated runtime control calls. This is
safer and avoids webhook URL storage, SSRF controls, secret encryption, delivery signatures, and
external retry semantics before they are needed.

### 4. Durable execution ownership

Django owns the durable control plane:

- Task and current status;
- initial and continuation Messages;
- monotonic event sequence;
- dispatch/outbox rows;
- execution attempts and their current-attempt fence under the canonical AgentSession lease;
- output projection and artifact deltas;
- cancellation intent;
- caller delegation and delivery state.

Astro owns execution:

- claim a ready dispatch;
- run the target agent turn;
- heartbeat the canonical AgentSession lease;
- publish coalesced output deltas;
- request interruption states; and
- settle the attempt and Task.

Celery accelerates dispatch and recovery but is not the source of truth. Redis may wake subscribers
but is not the replay log. A process-local task may be used only after a durable dispatch exists,
the Task AgentSession's canonical `runtime_run` lease is held, and a fenced attempt is claimed. If
that process dies, the session lease expires and recovery can issue another safe attempt.

Delivery is at least once. Exactly-once execution is not claimed. Every Task mutation from an
executor carries the active attempt and fence, and stale attempts are rejected. An ambiguous
failure after an uncheckpointed external side effect is not blindly retried; the attempt is marked
ambiguous/failed for explicit reconciliation.

The accepted hard cut keeps the AgentTask aggregate completely independent from Pod Manager. A Task
has no `BaseJob`, `JobRun`, `JobRunOutput`, or Pod Manager `Artifact` relationship and never mirrors
Job status or cancellation. An agent may invoke zero, one, or many independent jobs as tools and
record relevant results as ordinary Task Messages or A2A artifact parts.

Implementation purges pre-cutover AgentTask aggregate rows while preserving the independent Pod
Manager records. This destructive migration happens only in the coordinated maintenance cutover
with the normal pre-migration backup; this document does not delete data.

Dispatch delivery has three stages: ADR-027 wakes/checks a scaled-to-zero runtime with `GET /ready`;
Django then sends a bounded signed `dispatch available` signal even if the runtime was already
serving; Astro finally pulls and atomically claims the durable dispatch from Django. The signal is a
hint and its Task/dispatch UIDs are not authorization.

The wake stage reuses the existing ADR-027 durable operation, Celery machinery, readiness
observation, retries, and signed runtime credential boundary. A2A adds no parallel wake technology;
only the post-wake dispatch signal and durable claim are Task-specific.

### 5. Input and authorization interruption

Astro exposes trusted runtime controls equivalent to:

```text
task.request_input(reason, schema, prompt)
task.request_authorization(requirement_reference, prompt)
```

The host injects the active Task/attempt identity. The model cannot name an arbitrary Task. Calling
one of these controls atomically persists a structured status Message/event, moves the Task to the
interrupted state, and releases/settles the active attempt without treating it as failure.

Continuation appends a new idempotent Message to the same Task and context, creates a new dispatch,
and eventually a new execution attempt. Credentials and bearer tokens never travel in Task parts.
For `auth_required`, the Task stores only a safe requirement reference and explanation; credential
creation/refresh happens through the existing backend-owned provider authorization flow.

The enforced state machine is:

```text
submitted -> working | rejected | canceled
working -> input_required | auth_required | completed | failed | canceled
input_required -> submitted | canceled | failed
auth_required -> submitted | canceled | failed
terminal -> no transition
```

The interrupted-to-submitted transition occurs only with an accepted continuation Message. Claiming
the resulting dispatch moves it to working.

### 6. One replayable subscription implementation

Django adds a monotonic `last_event_sequence` watermark to the Task and returns an atomic Task
snapshot plus watermark. Event reads use `after_sequence` and preserve per-Task order.

Astro implements one `TaskEventStream` adapter used by:

- REST `tasks/{id}:subscribe`; and
- JSON-RPC SubscribeToTask.

The stream emits the current Task first, then maps durable status and artifact events to standard
Task status/artifact update events. It closes at a terminal state. A Redis notification may wake the
reader immediately; if a hint is lost, bounded polling of the durable cursor still recovers every
event. Django's current in-memory Channels layer is not a cross-replica authority and is not used as
one.

Authorization is rechecked from Task/session ownership on subscription and on resumed reads. A
protocol Task ID is an identifier, not an authority token.

### 7. Durable live artifacts

Django adds atomic output operations:

```text
create output
append bounded delta
finalize output
```

Each operation updates the current `AgentTaskOutput` projection and appends an immutable ordered
Task event containing the replay delta (`append`, `lastChunk`, parts, metadata, and artifact ID).
Astro coalesces model deltas before persistence, using configurable time/size thresholds. The exact
threshold is operational policy; a reasonable initial range is 100-250 ms or 4-16 KiB.

This produces live subscription updates without writing every token to PostgreSQL. Strict JSON
contracts remain buffered and validated; subscribers receive one final artifact rather than invalid
partial JSON.

### 8. Waking Agent A

For `resume_caller`, Django records the verified caller session, target Task/session, originating
message/turn key, completion policy, and last delivered Task event sequence. When the Task reaches
an actionable state, a durable caller-delivery row is created in the same transaction as that event.

A Celery dispatcher wakes the caller runtime through the existing internal runtime wake/control
path. Astro claims a caller-continuation lease and inserts a platform-authored event containing the
Task UID, state, and event cursor. Agent A retrieves the full result through `a2a.get_task` or
`a2a.wait_task` rather than receiving unbounded output in the wake payload.

Deliveries are idempotent by delegation plus triggering event sequence. If Agent A already has an
active turn, the continuation remains queued; two turns are never run concurrently for one caller
session. Failed wakes retry from the durable row.

## Backend Records Required

The exact Django class names may follow repository conventions, but the logical records are:

| Record | Purpose |
| --- | --- |
| `AgentTask.last_event_sequence` | Atomic snapshot watermark and next ordered event source |
| `AgentTaskDispatch` | Durable execute/resume outbox with deduplication and retry state |
| `AgentTaskExecutionAttempt` | Attempt number, claimant, current-attempt fence, outcome, sanitized failure |
| `AgentTaskOutput` additions | Revision/finalization state for current task artifact projection |
| immutable output event delta | Replayable append/finalize content associated with an ordered Task event |
| `AgentTaskDelegation` | Caller session, target Task/session, origin key, and completion policy |
| `AgentTaskCallerDelivery` | Idempotent actionable-state delivery and caller wake lifecycle |

Dispatch and attempt records are separate: one dispatch expresses work that must happen; attempts
record each lease-bound execution of that work.

## End-To-End Behaviors

### Poll policy

```text
Agent A -> a2a.send_message(task, poll)
        <- Task handle
Agent A -> a2a.wait_task(handle, cursor, bounded timeout)
        <- terminal/interrupted Task, or latest non-terminal Task + cursor
```

### Resume-caller policy

```text
Agent A -> a2a.send_message(task, resume_caller)
        <- Task handle; Agent A's current turn may end
Agent B -> durable status/output events
Django -> durable caller delivery -> wake Agent A
Agent A -> a2a.get_task(handle) -> handles result or interruption
```

### Input continuation

```text
Agent B -> task.request_input(...)
Django -> Task input_required + event -> wake Agent A
Agent A/user supplies response
Agent A -> a2a.send_message(task_uid=..., response_kind=task, ...)
Django -> continuation Message + Dispatch
Agent B -> new fenced attempt -> working -> terminal/interrupted
```

### Authorization continuation

```text
Agent B -> task.request_authorization(requirement_reference, ...)
Django -> Task auth_required + event -> wake Agent A/user
User completes backend provider authorization out of band
Agent A -> continuation Message/reference
Agent B -> new attempt uses backend-resolved credential; secret never enters A2A
```

## Rollout Order

Task mode must not be advertised through discovery until the durable path is complete.

1. Accept the coordinated Astro and Django ADRs and amend ADR 47: preserve its public v1 Message
   default while requiring explicit result selection in controlled clients and restoring standard
   `returnImmediately` handling.
2. Add Django dispatch, current-attempt fencing under the canonical AgentSession lease, event
   watermark, strict state machine, and recovery sweeper.
3. Add output create/append/finalize services and durable artifact deltas.
4. Change Astro Task execution to hold/heartbeat the AgentSession lease and claim/settle attempts;
   keep process-local scheduling only as an optimization.
5. Replace snapshot polling with the shared event-cursor subscription and add JSON-RPC
   SubscribeToTask.
6. Add structured input/auth interruption and Task continuation.
7. Publish required `response_kind`, the discriminated MCP result, Task lifecycle tools, and
   `completion_policy`; update SDK and CLI in the same coordinated release.
8. Add durable caller delegation/delivery and `resume_caller` wake handling.
9. Advertise Task support only after restart, lease-expiry, replay, cancellation, continuation, and
   caller-resume end-to-end tests pass.
10. Keep push notifications disabled; reconsider them only in a separate ADR driven by external
    interoperability requirements.

## Required Test Scenarios

- controlled clients reject missing `response_kind` without making a request;
- arbitrary public clients can omit the extension and can send standard `returnImmediately`;
- `responseKind: task` returns a Task and never a Message;
- a target Astro process dies after Task creation and another attempt recovers after lease expiry;
- stale attempt fences cannot append output or settle status;
- duplicate dispatch and continuation Messages are idempotent;
- cancellation works before claim, during execution, and after lease expiry;
- input/auth interruption releases execution and resumes only after a valid continuation;
- REST and JSON-RPC subscriptions emit the same ordered status/artifact sequence;
- a reconnect from an event cursor neither loses nor duplicates a logical event;
- coalesced artifact updates replay after process restart;
- strict JSON produces no invalid partial artifact;
- `resume_caller` wakes Agent A exactly once logically despite at-least-once delivery;
- an active Agent A turn causes queued, not concurrent, resumption; and
- no Task path stores provider credentials or enables public push notifications.

## Deferred Work

External A2A webhook push notifications are intentionally deferred. Re-enabling them still requires
durable configuration, encrypted authentication material, HTTPS/SSRF validation, signed delivery,
retry/dead-letter handling, idempotency, and external interoperability tests. None of that is needed
for the accepted internal A2A wake architecture.

## Protocol References

- A2A v1.0 specification: https://a2a-protocol.org/v1.0.0/specification/
- A2A streaming and asynchronous operations:
  https://a2a-protocol.org/latest/topics/streaming-and-async/
- A2A extensions: https://a2a-protocol.org/latest/topics/extensions/
