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
- local chat session discovery, platform-shaped chat history, and the local Agent identity;
- local direct A2A conversation discovery and bounded Message hydration;
- A2A message send/stream, task list/get/cancel/subscribe, push-notification compatibility routes,
  and JSON-RPC; and
- controlled internal dispatch and caller-delivery availability routes.

The exact methods and paths are frozen in `tests/contract/test_http_surface.py`. Wire examples and
schema behavior are tested rather than duplicated manually here.

### Request identity

`create_app()` declares request identity for the platform launcher in
`app.state.mainsequence_request_identity`: `{"installed": True, "mode": "assertion",
"public_ingress": ()}` when the runtime is hosted, and the same with `"mode": "local"` otherwise.
The launcher serves an application only with this declaration, read from the application object it
serves. The runtime is hosted when `MAINSEQUENCE_CALLER_AUTH_MODE=assertion` or when any of
`APP_NAME`, `FASTAPI_PUBLIC_BASE_URL`, `MAINSEQUENCE_CALLER_ASSERTION_ISSUER` or
`MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL` is set; see
[Settings and credentials](./settings.md#hosted-caller-authentication).

In hosted mode every request carries one platform-signed assertion in
`X-MainSequence-Caller-Assertion` (ADR 0019):

| Route | Accepted assertion |
| --- | --- |
| `/internal/*` (internal dispatch and caller delivery) | Platform assertion, `typ` `mainsequence-platform-assertion+jwt`, with no caller |
| Every other route, including `/health`, `/ready`, and `/version` | Caller assertion, `typ` `mainsequence-caller-assertion+jwt`, naming the caller in `sub` |

- A missing, invalid, expired, duplicated, or wrong-kind assertion gets 401. A key set that cannot
  be fetched gets 503. Both answers carry `{"detail": ...}` and `Cache-Control: no-store`.
- The runtime never falls back to `X-User-UID` or any other header.
- `OPTIONS` passes without an assertion. The launcher answers `/ms-health-deployment` and
  `/__mainsequence/healthz` itself.
- Handlers read the verified caller from `request.state.user` (`uid`, `team_uids`,
  `is_organization_admin`) and `request.state.user_uid`. On a platform assertion both are `None`.
- A user turn is stamped with the verified `sub`. An Agent turn keeps its provenance from the
  gateway's `X-Caller-*` headers, and `X-Caller-Kind` stays required on chat and A2A messages.

Outside hosted mode nothing is verified and every route behaves as before.

### Session ownership

In hosted mode, a request that addresses an existing session must come from the session's owner,
the User the platform recorded as `created_by_user_uid`, from an Organization admin
(`is_organization_admin` in the caller assertion), or, for a delegated child session, from the
workload User of the Agent that delegated to it. That last caller is admitted only when the
session's `parent_session_agent_uid` names an Agent, and the platform's directory
(`GET /api/v1/users/<sub>/`) shows the caller as a workload User whose `agent_uid` is that Agent.
The `X-Caller-*` headers never admit a caller, a failed lookup refuses the request, and the
admitted Agent is never a requester. Anyone else gets 403 before the runtime acts, on REST and
JSON-RPC alike. The check covers chat, the session model read, session cancellation,
A2A Message send and stream (the `contextId` session, and the session of a continued or existing
Task), Task get, cancel, subscribe, and list by `contextId`, and the extended Agent Card. A Task
list without `contextId` returns only Tasks of sessions the caller may address.

The runtime never creates a session in managed mode: the platform creates it and records its
owner. The platform's own `/internal/*` calls carry no caller and are not subject to the check.
These runtime admission rules differ from the platform's conversation and Task permissions; see
[the permission matrix](./security-model.md#conversation-and-task-permissions).

### The turn's requester

An Agent that an Organization admin enabled for it can act with the ordinary permissions of the
person whose request a turn is serving. The runtime never names that person; it proves which of its
own sessions it is working on, and the platform finds the person in its own records (ADR 0019,
section 9, and ADR 0021).

- **Turn start.** When a hosted runtime marks a turn active
  (`PATCH /api/v1/agent-sessions/<uid>/tau-runtime-activity/` with `runtime_activity` `working` and
  the new `active_turn_uid`), the answer is the usual runtime state plus `requester_user_uid`: the
  person the turn serves, or `null`. This answer is the only source of the person, for every kind
  of turn and whoever called. A chat or A2A Message turn sends the verified caller assertion of the
  request that started it in `X-MainSequence-Caller-Assertion`, beside its own credential: only on
  that transition, only while it is valid, and never in local mode or outside hosting. A turn that
  resumes a caller delivery names the delivery in `caller_delivery_uid` instead. A Task attempt
  sends neither.
- **Task creation and continuation.** When a hosted request creates a Task
  (`POST /api/v1/agent-tasks/`) or continues one (`POST /api/v1/agent-tasks/<uid>/continue/`), the
  runtime sends the request's verified caller assertion in the same header, under the same rules.
  The answer's `requester_user_uid` and `requester_identity_type` are the Task's records; the
  runtime does not take the person from them. The request's verified caller supplies the person's
  teams when the attempt's turn start names that caller.
- **Task dispatch and caller delivery.** `POST /internal/a2a/task-dispatch` and
  `POST /internal/a2a/task-caller-delivery` may carry `requester_user_uid` and
  `requester_identity_type`; the runtime does not take the person from them either.
- **Calls for the work.** While the turn serves a person, every call made for the work carries the
  person's delegation: a Main Sequence MCP call carries the turn's session proof under
  `mainsequence.ai/delegation/v1` in its private metadata, and a REST call through
  `platform_client()` sends the runtime's credential with `X-MainSequence-Acting-For-Session` (the
  turn's session), `X-MainSequence-Lease-Holder` and `X-MainSequence-Lease-Token` (the runtime's
  lease on it), to the platform base URL only. A hosted Main Sequence MCP call also names its
  session under `mainsequence.ai/caller-session-proof/v1`, whether or not it carries a delegation.
  A call to another application first sends
  `POST /api/v1/resource-releases/<release_uid>/resolve-runtime-access/`, with the delegation
  headers when the call carries the delegation and without them otherwise, then calls the returned
  `access.rpc_url` with `Authorization: Bearer <access.token>` only.
- **Housekeeping.** The runtime's own calls (the lease, turn activity, entries, snapshots and Task
  status) never carry a delegation.
- **Refusal.** A 403 whose JSON `code` is `requester_binding_invalid` or starts with
  `runtime_lease_` ends the delegation for the call, which raises a `PermissionError` with that
  code. It is never retried without the delegation.

The assertion, the lease token, the runtime credential and application tokens never appear in a
log line, a persisted entry, model context, a tool result, the UI stream or history.

### Declared application MCP endpoints

Declaring an application in the Agent's workflow file registers its MCP endpoint; it grants no
access. The platform resolves each declared application in the Agent's Environment and returns it
in the startup data as an `mcp_applications` entry with `name` and `resource_release_uid`; no UID
or URL is configured in the project. For each entry the runtime offers two tools:

- `<name>__list_tools` returns the application's tools: name, description, input schema and
  whether it only reads; and
- `<name>__call_tool` takes `tool` and `arguments` and calls that tool.

For each call the runtime obtains the application's RPC URL and a token through
`resolve-runtime-access`: for the person the turn serves, or the Agent's own when it serves nobody.
It opens an MCP session to `<rpc_url>/mcp` with that token, renews the token once on `401`, and
closes the session when the call ends. Without a person, the Agent's workload needs its own grants
on the application. A name must match `^[a-z][a-z0-9_]{0,39}$` and must not be `mainsequence`; an
invalid or repeated name fails the session load. Local mode has no declared applications.

A delegated call uses the person's ordinary permissions, including administrative ones; any other
call uses the Agent's own, and each operation decides what it needs. For configuration, write
risk, revocation, and the distinction between requester and workload authority, see the
[Security and access guide](./security-model.md#acting-for-the-person-acts_for_requester). Both
need the matching platform change; see [availability](./security-model.md#availability).

### Chat turns and client disconnects

A managed `/api/chat` turn is bound to the request that streams it: a client disconnect before the
terminal frame cancels the turn. A local turn outlives its request (ADR 0018). A disconnect only
detaches the stream; the turn runs to its durable end and stays visible through the local chat
history. Only `POST /api/chat/session/cancel` stops it. A local session runs one turn at a time,
and `POST /api/chat` for a session with a running turn returns 409 `session_busy`.

### Managed AgentTask execution

The platform owns the durable AgentTask state machine and route contract. A managed runtime lists the
Task's dispatches when needed, claims the selected dispatch through `dispatches/claim`, explicitly
starts the returned attempt through `attempts/start`, writes artifacts through
`outputs/create`, `outputs/append`, and `outputs/finalize`, then settles the attempt through
`attempts/settle`. Settlement returns an attempt record; the runtime reads the Task afterward for
the resulting protocol state. The SDK does not post attempt Messages or use combined mutation or
route aliases that the platform does not expose.

Local Task creation and continuation persist one complete Main Sequence binding Message with
`ROLE_REQUESTER` and ordered URI-array `extensions`. Managed Task creation and continuation
translate the A2A Message into the platform's snake_case `message_id`, `parts`, `metadata`, and
`reference_task_ids` fields. An empty `extensions` array is omitted so the backend supplies its
default; a nonempty array returns HTTP 400 before a managed Task mutation until the backend can
persist extension URIs. Object-valued extensions and other non-A2A-v1 shapes also return HTTP 400.

Caller delivery flows in the other direction. The platform sends the signed internal delivery signal,
including the canonical caller AgentSession UID as a selector. The runtime persists the platform
event idempotently by delivery UID and flushes session storage before returning success, then
schedules the caller continuation. A busy caller session returns conflict so the platform retains and
retries the delivery. The SDK does not read, claim, or settle caller-delivery records through
the platform.

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
remains outside the SDK's public FastAPI operation surface. In hosted mode
`/ready` is an application route like any other and needs a caller assertion;
platform probes use the launcher's endpoint.

### Local-mode route boundary

| Surface | Local behavior |
| --- | --- |
| Health, readiness, version | Supported; reports local mode and dependency readiness. |
| Chat stream | Supported; `sessionUid` may be omitted. The turn is recorded as a chat session and runs to its durable end if the client disconnects. |
| Session model and cancellation | Supported for an existing local session. |
| Mock chat | Supported. |
| Chat session list/history | Supported through `/api/local/v1/chat-sessions`; history uses the platform's projected-history shape, with text, reasoning, tool calls, and the running turn in `inProgressMessage`, for the authenticated process principal's sessions only. |
| Local Agent identity | Supported through `/api/local/v1/agent`, from the workspace's `.agents/agent_card.json`. |
| A2A Message send | Supported with a workspace-local context identity. |
| Direct Message conversation list/history | Supported through `/api/local/v1/conversations`; returns only the authenticated process principal's public requester/responder projection. |
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

In local mode `mainsequence_auth_source` names where the process takes its Main Sequence
credentials from: `cli` for the Main Sequence CLI session, `environment` for a token pair handed
to the process, or `env_file` for a pair read from the project `.env`. It is a name, never a
value, and it is `null` in managed mode.

## Failures

SDK errors use a stable JSON envelope with `ok=false`, an error code, message, and safe detail.
Backend status, lease conflicts, busy sessions, missing sessions, cancellation, output limits, and
invalid strict-JSON results retain distinct behavior covered by contract tests.
