# ADR 0014: Human-Readable A2A Task History and Execution Narratives

Status: Accepted — SDK implementation complete; deployed control-plane rollout pending

Date: 2026-09-26

Amends:

- [ADR 0002: Runtime and protocol contracts](./0002-runtime-and-protocol-contracts.md) by defining
  the public A2A Task `history` projection and its backend-neutral read contract;
- [ADR 0009: Backend model selection and Task timelines in Tau Board](./0009-tau-board-session-model-and-task-timeline.md)
  by replacing a raw event-centric Task detail with a human narrative; and
- [ADR 0013: Guaranteed A2A Task terminalization and failure observability](./0013-guaranteed-a2a-task-terminalization-and-failure-observability.md)
  by correlating each execution attempt with the exact Tau turn that produced its observable
  outcome.

## Context

Tau persists enough local information to reconstruct a Task, but Tau Board does not present that
information as a coherent unit of work. The current Task detail shows status fields, attempt and
artifact counts, low-level Task events, and related logs. It does not show the original requester
Messages or the complete final artifacts. The separate A2A action console can fetch the current
protocol Task, but it is not an execution inspector and it does not show the Tau turn that produced
the result.

Streaming makes the event-centric presentation especially misleading. One logical response is
persisted as one artifact with many monotonic revisions. Each coalesced append correctly creates an
`output_updated` event for durability and subscription replay, but presenting every revision as an
equal human event makes one response look like many outputs. Joining persisted text Parts with
synthetic newlines also corrupts text whose Parts are append fragments.

Four distinct histories must not be conflated:

1. **Task Message history** is the ordered public A2A communication associated with a Task: the
   initial requester Message, continuation Messages, and important responder/status Messages that
   the agent intentionally persists.
2. **Task artifacts** are the durable results produced by the Task. Streaming revisions are the
   mutation history of one artifact, not additional Messages or additional results.
3. **Tau execution history** is runtime-internal session state such as model output, recorded
   reasoning, tool calls, tool results, and turn lifecycle. It can support a local or authorized
   operational inspector but is not public A2A Task history.
4. **Task events and logs** are technical mutation, replay, and operational evidence. They explain
   persistence and transport but are not the Task conversation.

The A2A Task data model already separates `history` from `artifacts`. `historyLength` is a bounded
read preference; it is not permission to discard the durable Message sequence. Tau currently
persists requester Messages locally but projects only the latest Message through its backend model
and omits `history` from the public Task payload.

Local execution can read its SQLite Task and session records directly. Deployed execution depends
on a control-plane contract that returns the same ordered Task Messages and persists exact
attempt-to-turn correlation. The SDK must define the required contract without depending on a
particular control-plane implementation.

## Decision

### 1. Keep one explicit ontology

The SDK uses these terms consistently:

| Term | Meaning | Public A2A Task history |
| --- | --- | --- |
| Task Message | Requester or responder communication deliberately associated with the Task | Yes |
| Status Message | A Message explaining a current status, interruption, failure, or next action | Yes when deliberately persisted |
| Artifact | A final or incremental Task result | No; remains under `artifacts` |
| Artifact revision | One create, append, or finalize mutation of an artifact | No |
| Tau entry | Internal session entry used to resume and inspect runtime behavior | No |
| Task event | Ordered durable mutation/replay record | No |
| Structured log | Operational telemetry | No |

Task history never means model chain-of-thought, system instructions, raw tool traffic, logs, or
artifact chunks.

### 2. Project bounded A2A Task history

The public Task serializer gains an optional `history` array of complete A2A Messages. Each Message
has its canonical `messageId`, optional `contextId` and `taskId`, role, Parts, metadata, extensions,
and referenced Task IDs.

The SDK applies these rules everywhere the A2A operation accepts `historyLength`:

- omitted `historyLength` means the client imposes no limit; the server returns its configured
  default bounded tail, which is 100 Messages for this SDK;
- `historyLength: 0` performs no history-tail read and omits `history`; this does not remove the
  Task's current status Message or another explicitly documented latest-Message summary;
- a positive value returns at most that many latest persisted Task Messages;
- returned Messages are always ordered oldest to newest within the selected tail;
- negative and non-integer values are rejected;
- the SDK returns no more than 100 Messages for one Task projection, even when a caller imposes a
  larger bound, as allowed by the A2A server-side lower limit; and
- an empty selected history may be represented by an empty array only when a positive history was
  requested or the server default was applied.

This applies consistently to REST and JSON-RPC Task get, send/continue Task results, and Task list
operations that accept the standard field. List implementations must batch or prefetch bounded
history; they must not issue one backend request per listed Task.

The SDK's backend-neutral model gains an ordered Task Message type and an optional Task `history`
projection. Backend adapters provide a bounded tail read by Task UID. The local adapter reads the
SDK-owned Message table. A deployed adapter uses the authorized durable Task-message contract of
its control plane. For an omitted public `historyLength`, both adapters request the SDK's explicit
default of 100 from their storage layer; storage-level omission is not used to reinterpret the
public protocol. Task events are never scanned as a substitute Message database.

### 3. Persist the Messages needed to understand a Task

The following Messages are durable Task history:

- the initial requester Message that created the Task;
- every accepted, idempotent continuation requester Message;
- responder Messages that request input or authentication;
- responder Messages that explain a failed or rejected Task; and
- another responder/status Message only when the runtime explicitly marks it for durable history.

A normal completed Task does not duplicate its Artifact as a responder Message. The result remains
an Artifact. Transient progress updates do not become history unless explicitly selected for
persistence.

The settlement contract is a hard cut to one shape. A persisted `status_message` is either absent
or a complete Message in the approved Main Sequence Tau binding, with a stable non-empty
`messageId`, `role: "ROLE_RESPONDER"`, and non-empty `parts`. `taskId` and `contextId` are required
and must match the Task. Its optional `extensions` is an array of URI strings stored in the accepted
order; extension payload belongs in `metadata[extension_uri]`, and that URI must also occur in the
array. An ad hoc `{code, message, ...}`
status-detail object or object-valued `extensions` is rejected. No compatibility union survives the
cutover.

The SDK does not call those local direction names canonical A2A v1 roles. At an A2A v1 protocol
boundary it maps `ROLE_REQUESTER` to `ROLE_USER` and `ROLE_RESPONDER` to `ROLE_AGENT`; the reverse
mapping is applied when entering the Main Sequence binding. Message identity, Parts, extension URI
order, metadata, and references are not changed by that role translation. A2A v1 responses never
emit the local-only `ROLE_REQUESTER` or `ROLE_RESPONDER` values.

The component authoring the response allocates `messageId` once before settlement and reuses that
Message unchanged across retries. Runtime-authored interruptions and failures therefore arrive as
canonical Messages. A control plane that originates recovery or dispatch-failure communication is
the author of its Message and follows the same rule. Neither adapter manufactures a new Message
identity while reading a Task.

Message persistence is atomic with the Task-visible mutation it explains. Message sequence is
allocated by the durable Task authority, and the same protocol `messageId` cannot produce two
logical Messages. Replaying an accepted requester Message or settlement operation cannot append a
duplicate history row. Reusing a Message identity with changed content is an idempotency conflict.
The local adapter stores the validated binding Message directly; a deployed adapter sends the same
wire Message and maps it to the durable control-plane representation without changing its identity
or content.

Task status events carry only a reference to the same durable status Message, never a third copy of
its Parts or metadata. The local event store follows the same rule. A status-stream adapter treats
the event as an invalidation and loads the coherent Task snapshot or referenced Message before
building the public A2A status update. It does not depend on an embedded event
`payload.status_message`, including when resuming across the coordinated cutover.

Settlement replay is an exact read-only outcome, not another mutation attempt. The durable attempt
stores a canonical hash of Task/attempt identity, requested status, normalized status Message,
normalized outcome category, and sanitized failure detail with the first settlement. An identical
retry from the canonically authenticated runtime and recorded executor/holder returns the existing
attempt without requiring the already-cleared current-attempt fence or an old lease that remained
live. A changed normalized request is a conflict. A historical settled attempt without a stored hash
is non-replayable; adapters never infer identity from current status, Messages, events, or timestamps.

### 4. Correlate every execution attempt with one Tau turn

Timestamp inference is not an acceptable Task-to-execution join. Before starting a Task attempt,
the SDK allocates the Tau `turn_uid`; the same identifier is supplied to the attempt-start contract
and to `SessionRuntimeManager.prompt`. A started attempt cannot change its `turn_uid`.

The durable attempt projection exposes:

```text
turn_uid
entry_start_sequence
entry_end_sequence
turn_resolution
```

`entry_start_sequence` is the session's next durable entry sequence after the control plane has
reserved that turn. `entry_end_sequence` is the exclusive sequence boundary when the reservation
is resolved. `turn_resolution` is `pending`, `committed`, or `abandoned`, so an empty committed turn
is distinguishable from a failed turn that never committed.

Every Tau entry accepted while that reservation is active carries the same `turn_uid`. The storage
authority serializes reservation, entry append, commit, and abandonment on the Session and rejects
unscoped or differently scoped writes inside the interval. The interval proves ordering while the
entry tag proves ownership; neither is sufficient alone.

The Session runtime state distinguishes a generic active Tau turn from a Task reservation. An
ordinary direct-Message turn has an active turn UID but no Task-attempt owner. Attempt start
atomically associates its turn UID with the exact attempt UID; generic runtime-activity updates
cannot create, replace, or clear that owner.

A completed, input-required, or authentication-required settlement requires the exact matching
turn commit. A failed, rejected, or canceled attempt may atomically abandon an uncommitted turn,
capture its current exclusive boundary, and close the reservation. Any later append or commit for
that abandoned turn is rejected and cannot reopen or extend the attempt narrative.

Lease loss does not clear a Task-owned reservation. A replacement lease request receives a
retryable recovery-pending conflict until the Task authority resolves the old attempt as committed
or abandoned. Tau retries acquisition after recovery; it never asks a lease-reset path to discard a
Task-owned turn. Normal lease release is rejected while that owner is present, while expiry retains
the reservation for recovery. An ordinary direct-Message turn has no Task owner, never invokes Task
recovery, and retains the generic expired-lease reset/release behavior. Local mode enforces the same
distinction and recovery outcome.

Retries use distinct attempt and turn UIDs. A Task may therefore contain several attempt narratives
without mixing their entries. Local SQLite stores the same correlation. A deployed control plane
must persist and return the correlation under the existing Task and session authorization; the
identifiers add no grant, lease, or bearer capability.

### 5. Make Tau Board's Task page the human inspection surface

Tau Board Task detail is a read-only projection with five layers:

1. **Overview** — state, created/status/terminal times, duration, attempts, recovery, failure, model
   and provider when safely available.
2. **Conversation** — ordered Task Messages rendered by role and Part type.
3. **Result** — complete final Artifacts with append text concatenated byte-for-byte and structured
   data formatted as JSON.
4. **Execution** — a normalized attempt/turn narrative built from exact correlation, retained Tau
   entries, and safe structured telemetry.
5. **Technical** — attempts, raw Task events, artifact revisions, logs, and protocol JSON.

The execution narrative uses human event kinds such as request received, attempt started, model
turn, tool call, tool result, interruption, artifact finalized, recovery action, and Task settled.
It does not dump raw entry JSON into the primary view.

Artifact revisions remain durable and individually expandable under Technical. The primary view
groups them by artifact and displays one lifecycle row with revision count, byte size, finalization
state, and final content. A final empty revision is shown as a finalization marker, not as an empty
response.

The action currently labelled **Open in A2A** becomes **Open in A2A Console**. It transfers the
Task/context selection to the action composer but is not required to understand the Task.

### 6. Separate Task outcome from nested tool outcomes

Tau Board displays the A2A Task state and nested tool/domain outcomes independently. A Task may
truthfully be `completed` because the agent handled the request and produced an Artifact while a
tool call failed and the Artifact explains that failure.

The Board must therefore support a presentation such as:

```text
Agent Task       Completed
Tool execution   Failed
Result           Failure explanation returned
```

The SDK does not automatically fail a Task merely because one tool result reports an error. An
agent can recover, choose another tool, or explain the failure. A domain operation that must control
the outer Task state needs its own explicit outcome contract; this ADR does not infer one from
arbitrary tool output.

### 7. Keep internal reasoning private and bounded

The public A2A Task and `history` never expose Tau entries, model reasoning, tool arguments/results,
system prompts, logs, credentials, paths, or provider payloads.

An authorized execution inspector may display retained internal activity only through a safe
projection. Recorded reasoning is local/operational data, honours provider redaction markers, is
collapsed by default, and is unavailable when the underlying entry was redacted or not retained.
Tool arguments and results use existing payload bounds and secret redaction. The Board never claims
that unavailable reasoning can be reconstructed.

### 8. Preserve ownership boundaries

The SDK owns:

- public A2A history request parsing and Task serialization;
- local Task Message, artifact, event, attempt, and turn-correlation persistence;
- transport-independent normalization of Task conversation, results, and execution trace;
- Tau Board's read-only Task inspector; and
- conformance tests proving local/deployed parity.

A deployed Task control plane is required to provide:

- durable ordered Task Messages;
- an authorized bounded tail read with complete canonical Message fields;
- a bounded latest-Message summary path, including snapshots that do not materialize the full
  conversation merely to find its latest Message;
- atomic persistence of selected responder/status Messages;
- immutable attempt-to-turn correlation, per-entry turn identity, and committed-or-abandoned entry
  boundaries;
- an explicit runtime-state Task-attempt owner that distinguishes Task reservations from ordinary
  Tau turns;
- replacement-lease fencing that resolves the previous attempt turn before issuing a new lease;
- atomic settlement fingerprints plus an authorized exact-replay read that creates no duplicate
  Message, event, delivery, timestamp, or transition; and
- Task/attempt authorization identical to the existing aggregate.

The SDK ADR does not prescribe the control plane's repository, framework, model names, route
implementation, migrations, or deployment process.

## Delivery Phases and Gates

### H0: Contract fixtures and current-behavior inventory

- Freeze Message, history, Artifact, revision, attempt-correlation, and normalized-trace shapes.
- Inventory every REST and JSON-RPC operation that accepts or returns Task history.
- Record which trace data is public, inspector-only, redacted, or unavailable.

Gate: contract tests distinguish Messages, Artifacts, entries, events, and logs and fail if internal
execution data appears in public Task history.

### H1: Complete local Task conversation and result

- Add local ordered Task-history reads.
- Return Task Messages and full Artifact parts to Board.
- Concatenate append fragments correctly and collapse revisions in the primary view.
- Rename the A2A navigation action.

Gate: a Task is understandable from Request and Result without opening A2A Console; streamed text
is reconstructed byte-for-byte; raw revisions remain available.

### H2: Exact local and deployed attempt-to-turn correlation

- Allocate the turn UID before attempt start.
- Reserve the turn before capturing its start boundary.
- Associate the reservation with the exact Task attempt; never infer Task ownership from a generic
  active turn UID.
- Persist the turn UID on every entry accepted during the reservation.
- Persist committed or abandoned attempt correlation and entry boundaries locally.
- Reject unscoped/interleaved entries and late append/commit after abandonment.
- Refuse replacement lease acquisition while a Task-owned turn is unresolved; resolve that turn
  through Task recovery before retrying acquisition. Preserve generic reset for ordinary turns.
- Refuse normal lease release only while a Task-owned turn is reserved and retain its expired state
  for recovery; ordinary turns retain generic release semantics.
- Add the backend-adapter contract and deployed conformance fixtures.

Gate: adjacent Tasks in one session and retries of one Task never share execution entries; no join
uses timestamps or text matching; commit-versus-failure races resolve to one immutable committed or
abandoned turn; lease loss cannot erase a reservation or block the Session after recovery.

### H3: Human execution narrative

- Project correlated entries, attempts, events, and logs into safe trace items.
- Display Task and nested tool outcomes independently.
- Keep raw technical evidence behind explicit expansion.

Gate: a requester can explain what was asked, what Tau did, what tools returned, and what result was
produced without reading raw JSON; redacted/private data is not exposed.

### H4: Public A2A history parity

- Implement `historyLength` for REST and JSON-RPC operations.
- Replace every ad hoc settlement status object with one complete Main Sequence binding responder
  Message and reject the legacy shape.
- Store `extensions` as an exact URI array with payload under metadata; reject object-valued input.
- Map local requester/responder direction values to A2A v1 `ROLE_USER`/`ROLE_AGENT` at public
  protocol boundaries.
- Persist selected responder/status Messages idempotently without read-time identity generation.
- Make status events reference the durable Message and reload it/snapshot during stream projection.
- Verify identical history semantics in local and deployed modes.

Gate: positive, zero, omitted, invalid, truncated, continuation, interruption, failure, legacy-shape
rejection, settlement replay, and changed-payload conflict cases pass the same contract suite in
both modes; role/extension fixtures match the A2A v1 schema at the public boundary; no new status
event embeds Message content; query assertions prove zero does no history-tail read and
snapshot/latest projection does not scan the full conversation.

### H5: Documentation and release verification

- Update the runtime contract, public API, Board guide, A2A runtime skill, local-development skill,
  troubleshooting, testing guide, and changelog.
- Run Board, protocol, backend-adapter, clean-install, and distribution gates.

Gate: packaged documentation uses the accepted ontology, the Board remains read-only, and no
example calls Task events or Tau entries Task Message history.

## Consequences

### Positive

- Users can understand a Task without navigating among raw state, A2A actions, and logs.
- A2A multi-turn history becomes standards-aligned and consistent across execution modes.
- Artifact streaming remains durable without overwhelming the human timeline.
- Exact correlation prevents trace contamination between Tasks and retries.
- Public protocol data stays separate from sensitive runtime internals.

### Costs

- Local state needs a schema migration for attempt/turn correlation.
- Deployed mode needs coordinated control-plane contract support before H2/H4 can be declared
  complete.
- Every interruption, failure, rejection, recovery, and dead-letter producer must move from ad hoc
  status JSON to complete Message Parts, extension URI arrays, and metadata.
- Public A2A projection needs an explicit mapping from the approved Main Sequence direction values
  to A2A v1 roles.
- Lease acquisition can briefly return recovery-pending while a lost executor's Task-owned turn is
  resolved; ordinary turns do not enter Task recovery.
- Status event consumers must reload the snapshot/referenced Message instead of reading an embedded
  body.
- History on Task lists needs bounded batch loading to avoid N+1 reads.
- Trace projection and redaction require dedicated tests rather than rendering raw stored JSON.

## Rejected Alternatives

### Treat Task events as Message history

Rejected because events are mutation/replay records, can omit complete Message content, contain
unrelated changes, and require unbounded scanning to answer a bounded history query.

### Put reasoning and tool traffic in public Task history

Rejected because Task history is protocol communication, not internal execution state. This would
leak sensitive implementation details and create an unstable public contract.

### Delete or stop persisting output revisions

Rejected because revisions are required for durable streaming, replay, ordering, and debugging.
The defect is primary-view presentation, not revision persistence.

### Join Tasks to session entries by timestamp

Rejected because retries, delayed persistence, clock differences, and adjacent turns can produce an
incorrect trace. Correlation must use immutable identifiers and sequence boundaries.

### Treat a sequence interval as proof of turn ownership

Rejected because a sequence range proves ordering only. Without a reserved turn and a durable
`turn_uid` on every entry, an unrelated writer could place entries inside the range. Inspection
filters by both the immutable turn identity and the half-open sequence interval.

### Derive a Message from an ad hoc status object while reading

Rejected because `{code, message, ...}` has no durable protocol identity, role, Parts, or declared
extension mapping. Read-time conversion can generate a different `messageId` on each projection and
cannot become ordered, idempotent Task history. The response author must create the Message once.

### Accept both status-detail objects and canonical Messages

Rejected because a union leaves every persistence and rendering path unable to know whether it owns
a status annotation or a conversation Message. The coordinated release uses one canonical shape.

### Call `ROLE_RESPONDER` a canonical A2A v1 role

Rejected because A2A v1 uses `ROLE_AGENT` for server-originated Messages and URI arrays for Message
extensions. Main Sequence binding roles remain explicit local direction values and are translated at
the protocol boundary.

### Clear a pending turn during replacement lease acquisition

Rejected because lease ownership does not resolve the attempt. Recovery must first commit or
abandon the exact turn; only then may a new lease be issued.

### Embed the complete status Message in every status event

Rejected because the Task and ordered Message record already own that content. Events reference the
Message, and adapters reload a coherent projection when producing a public status update.

## Acceptance Criteria

1. Task detail displays the original requester Messages and complete final Artifacts directly.
2. Omitted, zero, and positive `historyLength` values have the A2A-defined semantics and identical
   bounded-tail results in local and deployed modes.
3. Important interruption/failure responder Messages remain available after later status changes.
   Each was accepted with one stable `messageId`; no Message identity is generated during reads.
4. Public Task history contains no reasoning, tool internals, logs, prompts, credentials, or
   artifact revisions.
5. Streamed text is reconstructed byte-for-byte without synthetic separators.
6. Many `output_updated` events appear as one Artifact lifecycle in the primary view and remain
   individually inspectable under Technical.
7. Every started attempt has one immutable turn UID; every entry accepted during its reservation
   carries that UID; resolved attempts expose exact entry sequence boundaries and a `committed` or
   `abandoned` resolution.
8. Concurrent/adjacent Tasks and retries cannot contaminate each other's execution narrative;
   unscoped or wrong-turn writes are rejected while a turn is reserved.
9. Task state and nested tool outcomes are displayed independently.
10. Tau Board remains read-only and opening a Task cannot mutate runtime or Task state.
11. Local and deployed conformance suites pass before the feature is documented as complete.
12. Settlement rejects ad hoc `{code, message, ...}` objects and accepts only absent or complete
    Main Sequence binding `ROLE_RESPONDER` Messages with non-empty Parts and URI-array extensions;
    `taskId`/`contextId` must match the Task, and public A2A v1 projection emits `ROLE_AGENT` while
    preserving the Message content.
13. Exact settlement replay returns the existing attempt without writes after its active fence is
    cleared; changed requests conflict and pre-cutover attempts without a fingerprint are not
    guessed.
14. Failed, rejected, or canceled settlement can abandon an uncommitted turn atomically; a later
    append or commit for that turn is rejected and cannot change the recorded interval.
15. Runtime state explicitly distinguishes ordinary active turns from Task-owned reservations.
    Replacement lease acquisition cannot clear a pending Task turn; it retries only after recovery
    records that turn as committed or abandoned. Normal release is rejected only for a Task-owned
    reservation, while ordinary turns never invoke Task recovery and retain generic lease reset and
    release semantics.
16. Local and deployed status events reference one durable Message without embedding it, and status
    stream projection reloads the snapshot or referenced Message.

## References

- [A2A v1.0 protocol specification](https://a2a-protocol.org/v1.0.0/specification/)
- [A2A canonical protobuf](https://github.com/a2aproject/A2A/blob/main/specification/a2a.proto)
