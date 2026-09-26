# Runtime and HTTP Contract

`ms-tau` and `create_app` expose the same FastAPI application. Managed durable requests require an
existing backend session identifier. Local mode instead creates a workspace-scoped local session
lazily and never registers an Agent or AgentSession.

## Execution paths

Durable execution:

```text
request with session uid
  -> authenticated bootstrap, lease, provider evidence, history/snapshot
  -> workspace-bound Tau CodingSession
  -> Tau events translated to the selected HTTP/SSE/A2A transport
  -> atomic entry batches and turn settlement
```

Local development execution:

```text
chat or public A2A request with a local context
  -> user-JWT-authenticated provider evidence and credential hydration
  -> workspace-bound Tau CodingSession with configured tool sources
  -> SQLite entries, leases, snapshots, and A2A Task/message/artifact/event state
  -> no Agent/AgentSession or platform task-persistence calls
```

Local state defaults to `~/.tau/mainsequence/<workspace-hash>/runtime.sqlite3`. It is never
uploaded when local mode is disabled. Main Sequence authentication, provider hydration, and model
inference remain remote. When MCP is enabled, its side effects are real platform side effects.

## Operations

The executable operation contract covers:

- `/health`, `/ready`, and `/version`;
- `/api/chat`, mock chat, session model, and cancellation;
- A2A message send/stream, task list/get/cancel/subscribe, push-notification compatibility routes,
  and JSON-RPC; and
- controlled internal dispatch and caller-delivery availability routes.

The exact methods and paths are frozen in `tests/contract/test_http_surface.py`. Wire examples and
schema behavior are tested rather than duplicated manually here.

### Managed AgentTask execution

Django owns the durable AgentTask state machine and route contract. A managed runtime lists the
Task's dispatches when needed, claims the selected dispatch through `dispatches/claim`, explicitly
starts the returned attempt through `attempts/start`, writes artifacts through
`outputs/create`, `outputs/append`, and `outputs/finalize`, then settles the attempt through
`attempts/settle`. Settlement returns an attempt record; the runtime reads the Task afterward for
the resulting protocol state. The SDK does not post attempt Messages or use combined mutation or
route aliases that Django does not expose.

Local Task creation and continuation persist one complete Main Sequence binding Message with
`ROLE_REQUESTER` and ordered URI-array `extensions`. Managed Task creation and continuation
translate the A2A Message into Django's snake_case `message_id`, `parts`, `metadata`, and
`reference_task_ids` fields. An empty `extensions` array is omitted so the backend supplies its
default; a nonempty array returns HTTP 400 before a managed Task mutation until the backend can
persist extension URIs. Object-valued extensions and other non-A2A-v1 shapes also return HTTP 400.

Caller delivery flows in the other direction. Django sends the signed internal delivery signal,
including the canonical caller AgentSession UID as a selector. The runtime persists the platform
event idempotently by delivery UID and flushes session storage before returning success, then
schedules the caller continuation. A busy caller session returns conflict so Django retains and
retries the delivery. The SDK does not read, claim, or settle caller-delivery records through
Django.

### Deployment readiness

Managed deployments expose the platform-owned `GET /ms-health-deployment`
endpoint through Pod Deployment Orchestrator. Projects do not create or
scaffold that route. `ms-tau-sdk` automatically publishes the Tau readiness
predicate consumed by the launcher: ASGI startup must be complete, the runtime
manager must be ready and not draining, and an optional project readiness hook
must return true.

Applications may register that optional, side-effect-free predicate once with
`register_deployment_readiness_hook(app, hook)`. Hook failures and timeouts are
reported only as sanitized not-ready responses. The existing `/ready` route is
a compatibility alias over the same Tau predicate; the reserved platform route
remains outside the SDK's public FastAPI operation surface.

### Local-mode route boundary

| Surface | Local behavior |
| --- | --- |
| Health, readiness, version | Supported; reports local mode and dependency readiness. |
| Chat stream | Supported; `sessionUid` may be omitted. |
| Session model and cancellation | Supported for an existing local session. |
| Mock chat | Supported. |
| A2A Message send | Supported with a workspace-local context identity. |
| A2A Task send/stream/list/get/cancel/subscribe/continue | Supported through local SQLite. |
| Local Agent Card | Supported; advertises Message, Task, and streaming without platform registration. |
| Outbound A2A through MCP | Available when MCP is enabled; Message and polled Task flows use authenticated-user semantics. |
| Internal backend dispatch/caller delivery | `local_mode_capability_unsupported`. |
| Platform discovery, push notifications, `resume_caller` | Unsupported without explicit platform registration/callback support. |

An A2A protocol Task does not require a platform AgentSession. Only the remaining unsupported
surfaces require registered platform routing or callback identity, and local mode does not create
hidden records to satisfy them. Incoming local Message and Task calls do not require managed
gateway `X-Caller-*` headers: the runtime records workspace-local provenance, canonicalizes each
caller-supplied `contextId` into the workspace session namespace, and preserves the caller's public
`taskId`. Local Task rows, messages, artifacts, attempts, and event sequences survive process
restart. The SDK reconciles unclaimed `submitted` Tasks at startup and periodically. It expires a
stale `working` attempt only after its local session lease is no longer live. Because project tools
may already have produced external effects, an uncheckpointed stale attempt becomes a terminal
`failed` Task with an ambiguous-outcome classification instead of being executed twice. Outbound
MCP Task workflows use `poll`; `resume_caller` is rejected in local mode.

### Task terminalization and failure

The Task state is the only terminality authority. `completed`, `failed`, `canceled`, and `rejected`
are terminal; `input_required` and `auth_required` are interrupted and resumable. There is no
parallel terminal-status flag.

An ordinary execution failure is persisted as `failed` with a complete responder Message in
`Task.status.message`. Safe machine-readable failure evidence is under the Main Sequence
task-failure metadata extension and includes code, category, retryability, attempt number, and
correlation ID. Raw exception strings, prompts, tool arguments/results, credentials, and paths are
not failure payloads.

After persistence succeeds, streaming emits the authoritative terminal failed status update with
`final=true`. If persistence itself fails, the stream reports `task_terminalization_unknown`
instead of fabricating a failed Task; the durable recovery owner resolves it. Managed recovery
belongs to the backend dispatch system and must expire stale attempts, bound redispatch, handle
ambiguous outcomes, and terminalize exhausted recovery. The SDK does not run a competing managed
dispatcher.

`configuration.returnImmediately=false` uses a bounded server wait. Reaching that bound returns
the latest non-terminal Task handle and leaves execution running. Caller disconnect and transport
timeout never imply Task cancellation.

### Task conversation, result, and execution correlation

A Task has four separate durable projections: ordered Message history, current/final Artifacts,
Tau execution entries, and technical Task events/logs. Public `Task.history` contains only complete
A2A Messages. A completed Artifact is not duplicated as a responder Message, and streaming
Artifact revisions are not additional Messages. `historyLength` reads a bounded latest tail and
returns it oldest-to-newest; zero skips the history read entirely.

The Main Sequence storage binding persists requester/responder direction and maps it to public A2A
v1 `ROLE_USER`/`ROLE_AGENT` only at the protocol boundary. Status Messages have stable identities,
non-empty Parts, matching Task/context IDs, and URI-array extensions whose payload is stored under
Message metadata. A status event references that durable Message; stream projection reloads the
coherent Task instead of trusting an embedded event copy.

Before an attempt starts, Tau allocates its immutable turn UID. Attempt start reserves that turn
on the Session and captures `entry_start_sequence`; every accepted entry carries the same UID.
Commit or abandonment captures the exclusive `entry_end_sequence` and immutable resolution. Task
settlement that reports completion or a resumable interruption requires the matching commit;
failure, rejection, or cancellation may abandon a pending turn atomically. Later writes to an
abandoned turn are rejected.

Runtime state distinguishes an ordinary direct Tau turn from a Task-owned reservation with the
attempt owner UID. Lease expiry never clears a Task reservation. A replacement lease receives a
recovery-pending conflict until Task recovery commits or abandons that exact turn; ordinary turns
retain normal lease behavior. An identical settlement retry returns the stored attempt without
another mutation, while any changed normalized settlement conflicts.

The SDK models and sends the same fields in managed mode. A managed control plane must implement
the bounded Message endpoint, Task-list history projection, turn ownership/boundaries, per-entry
turn UID, and settlement fingerprint contract before the managed feature is available; the SDK
does not infer missing history or correlation from events, timestamps, or text.

## Effective composition diagnostics

Health reports safe process state: tool exclusion settings and source counts, session counts,
readiness, MCP catalog counts, snapshot counts, project-extension counts and errors, the effective
tool-catalog digest, Task recovery policy, and local pending/working/recovery counters. It never
reports credential values or prompt content.

## Failures

SDK errors use a stable JSON envelope with `ok=false`, an error code, message, and safe detail.
Backend status, lease conflicts, busy sessions, missing sessions, cancellation, output limits, and
invalid strict-JSON results retain distinct behavior covered by contract tests.
