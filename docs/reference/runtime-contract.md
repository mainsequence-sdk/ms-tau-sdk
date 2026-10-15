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
- `/api/chat`, mock chat, session model, the model-provider catalog, and cancellation;
- local chat session discovery, platform-shaped chat history, and the local Agent identity;
- local direct A2A conversation discovery and bounded Message hydration;
- Tau Board's local Agent inspection and project-tool workbench;
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
- Handlers read the verified caller from `request.state.user` (`uid`, `is_organization_admin`,
  and `in_teams()`, see [Team membership](#team-membership)) and `request.state.user_uid`. On a
  platform assertion both are `None`.
- A user turn is stamped with the verified `sub`. An Agent turn keeps its provenance from the
  gateway's `X-Caller-*` headers, and `X-Caller-Kind` stays required on chat and A2A messages.

Outside hosted mode nothing is verified and every route behaves as before.

### Team membership

`await request.state.user.in_teams(team_uids, *, max_age_seconds=None)` returns which of
`team_uids` the verified caller is an active member of, as a `frozenset` of canonical team UIDs. A
turn's requester has the same method while it is the verified caller of the request the turn runs
in. The runtime asks the platform with its own credential and the request's caller assertion, in
calls of at most 100 teams, and only when application code calls it.

- Within one request, the same question is asked once.
- Across requests, an answer is reused while it is younger than `TAU_TEAM_MEMBERSHIP_CACHE_SECONDS`
  (default `0`: no reuse), or than `max_age_seconds` when the call gives one.
- `max_age_seconds=0` always asks the platform and stores the new answer. Use it before acting on a
  membership that must be current, for example before a write.
- Answers are kept per release and Organization Environment, then person and team, at most 10,000
  per process.
- A call that fails raises `BackendError`; it never answers "not a member". Outside a hosted
  request with a verified caller, after that request ended, or once its assertion expired before a
  call it needs, `in_teams()` raises `TauSDKError`.

`team_uids` on the verified caller and on the requester is deprecated and warns when read. It
raises when the assertion carried no team list. Use `in_teams()`.

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

### MCP per-call response deadlines

Every SDK MCP connection, including `MainSequenceMCPClient`, accepts
`call_tool(..., timeout_seconds=N)` with a finite, positive response deadline. It replaces the
session's default wait when the queued call reaches that session. The HTTP response read follows
that call's lifetime, so delayed headers, JSON bodies and SSE data can arrive past the backend
read limit. Connect, write and pool limits, and initialization/catalog/resource read limits still
apply. Concurrent calls keep their own response deadlines without changing shared HTTP settings
or private metadata.

Expiration and caller cancellation release the local HTTP response, leave other calls running,
and never replay the operation. They do not imply remote cancellation or prove its outcome.
Cancelled queued calls are skipped. A standalone call has no implicit runtime turn limit; a call
inside a turn still ends when the turn ends. See ADR 0022's per-call deadline amendment.

The platform declares the time limit of each of its MCP tools in the tool's `_meta` under
`mainsequence.ai/timeout-seconds/v1`, the key an application tool uses. A Main Sequence MCP tool
the model calls waits that long for its answer; a tool that declares nothing, or a value that is
not a finite number greater than zero, waits the session default,
`MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS`. A direct `a2a.send_message` therefore waits as
long as the platform does for the target Agent's answer. A deadline only sets how long the runtime
waits; it never extends the platform's own limit for the operation.

### Declared application MCP endpoints

Declaring an application in the Agent's workflow file registers its MCP endpoint; it grants no
access. The platform resolves each declared application in the Agent's Environment and returns it
in the startup data as an `mcp_applications` entry with `name` and `resource_release_uid`; no UID
or URL is configured in the project. For each entry the runtime offers four tools:

- `<name>__list_tools` returns the application's tools: name, description, input schema and
  whether it only reads. It also says when the application publishes skills or resources;
- `<name>__call_tool` takes `tool` and `arguments` and calls that tool;
- `<name>__list_resources` returns the skills the application publishes, each with the URI of its
  `SKILL.md`, its name, its description and its files with their sizes, and the other resources it
  advertises; and
- `<name>__read_resource` takes the `uri` of one of them and returns its content.

For each call the runtime obtains the application's RPC URL and a token through
`resolve-runtime-access`: for the person the turn serves, or the Agent's own when it serves nobody.
It opens an MCP session to `<rpc_url>/mcp` with that token, renews the token once on `401`, and
closes the session when the call ends. Without a person, the Agent's workload needs its own grants
on the application. A name must match `^[a-z][a-z0-9_]{0,39}$` and must not be `mainsequence`; an
invalid or repeated name fails the session load. Local mode has no declared applications.

The session first asks `server/discover` on MCP protocol revision `2026-07-28`, where an
application declares the MCP Skills extension (`io.modelcontextprotocol/skills`). An application
that answers that question with an error or an HTTP client error status, or does not answer it
within ten seconds, is opened with `initialize`, as before. Skills are listed with `skills/list`
only when the application declares the extension; without it, skill files are ordinary resources.
`<name>__read_resource` reads only what the application lists. A file of a listed skill must be
named in the skill's file list, and its bytes must have the listed size and SHA-256 digest;
otherwise none of its content is returned and the result's `failure` is `unverified`. A skill the
application generates (`"dynamic"`) lists no files: only its `SKILL.md` is read, unchecked. Any
other URI must be in the application's resource list. Each read names the application, and the
skill for a skill file; binary content is not shown, only its type. A published skill is never
added to the project's skills and none of its frontmatter, such as `allowed-tools`, is applied
(ADR 0023).

Each tool may declare how long it runs, in seconds, in its `_meta` under
`mainsequence.ai/timeout-seconds/v1`. `<name>__call_tool` reads the application's tool list in the
session it opens, then waits for the call that long, or `TAU_MCP_TOOL_TIMEOUT_SECONDS`
(default `60`) when the tool declares nothing; the turn's own limit still applies. Opening the
session waits `MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS` for each request. A failure reaches
the model in plain words that name the time-out, status or refusal, never the error's own text.
When opening the session fails on the connection, a time-out or a server error, it is opened once
more; a call that was sent is never sent again. See
[Application tool time limits](../guides/application-tool-time-limits.md).

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

### Local chat sessions

Local mode records every `POST /api/chat` session and serves it back after a UI reload or a Tau
restart:

```text
GET /api/local/v1/chat-sessions?limit=50&cursor=<opaque>
GET /api/local/v1/chat-sessions/{sessionUid}/history
GET /api/local/v1/agent
```

The list returns the authenticated local process user's sessions, newest activity first, each with
its canonical `sessionUid` (the value of `X-Agent-Session-Uid`), title, message count, latest-text
preview, creation and activity times, and whether a turn is `working`. The history returns the
envelope of the platform's `GET /api/v1/agent-sessions/{uid}/history/`: `version`, `session`,
`messages`, and `inProgressMessage`. Messages carry user text, assistant text, reasoning, and tool
calls with their arguments, results, and `isError`. A turn still running is returned in
`inProgressMessage` with session status `running`. Continue a session by sending its `sessionUid`
to `POST /api/chat`.

`GET /api/local/v1/agent` returns `{name, displayName, description}` from the workspace's
`.agents/agent_card.json`, or nulls without a readable card. All three routes return 409 in managed
mode, where the platform owns sessions and history.

### Local direct A2A conversations

Local mode exposes an SDK-owned extension for direct `message:send` conversations:

```text
GET /api/local/v1/conversations?limit=50&cursor=<opaque>
GET /api/local/v1/conversations/{contextId}/messages?limit=100&beforeSequence=<n>
```

The first route lists the authenticated local process user's workspace conversations with stable
canonical context IDs, deterministic titles, message counts, latest-text previews, activity times,
and bounded cursor pagination. The second returns persisted public requester/responder Messages
oldest-to-newest within the selected tail. Reuse the listed `contextId` in
`POST /api/a2a/v1/message:send` to continue after a UI or Tau restart.

This extension is not an A2A v1 method and is unavailable in managed mode. Its Messages are not
derived from Tau entries and never include system instructions, reasoning, tool activity, events,
or logs. A2A Task conversations remain under `Task.history`.

### Direct Message time limit

A direct Message, `message:send` answered with a Message and JSON-RPC `message/send` in Message
mode, runs its turn while the request waits. The platform gives a direct Message 90 seconds, and
it passes that limit to the runtime it hosts in `MAINSEQUENCE_A2A_MESSAGE_LIMIT_SECONDS`. The
limit runs from when the runtime receives the request and covers the whole request, including
output-contract repair turns. A turn also ends at `MAINSEQUENCE_TAU_TURN_TIMEOUT_SECONDS` when
that comes first. No client or runtime setting extends the platform's limit. Without the
variable, as in local mode, a direct Message ends only at the turn limit.

When either limit expires, the runtime stops the turn, the way it stops any turn that reaches its
time limit, and answers HTTP 504 with media type `application/a2a+json`. A request that has used
the whole platform limit before its turn could start starts no turn and gets the same answer:

```json
{
  "error": {
    "code": 504,
    "status": "DEADLINE_EXCEEDED",
    "message": "The Agent did not answer the Message within 90 seconds. Use Task mode for longer work.",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "MESSAGE_RESPONSE_LIMIT_EXCEEDED",
        "domain": "mainsequence.ai",
        "metadata": {"limitSeconds": "90"}
      }
    ]
  }
}
```

`limitSeconds` is the limit that expired, as a string. JSON-RPC `message/send` answers a JSON-RPC
error with code `-32000`, the same message, and the same `ErrorInfo` list in `data`. Work the turn
already did, such as tool calls, is not rolled back and is never replayed. Work that may take
longer uses Task mode (`configuration.responseKind: "task"`).

The platform's limit also ends the server wait of a Task-mode `message:send` with
`returnImmediately: false`: it waits for at most `MAINSEQUENCE_TAU_A2A_TASK_WAIT_TIMEOUT_SECONDS`
and never past the platform's limit, then returns the Task as it stands while the Task keeps
running. `message:stream`, which is always a Task, `/api/chat`, caller delivery, Task dispatch and
Task turns have no platform Message limit; `MAINSEQUENCE_TAU_TURN_TIMEOUT_SECONDS` bounds their
turns.

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
endpoint through the platform launcher. Projects do not create or
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
| Session model and cancellation | Supported. Before a session's first turn, the session model read reports the configured `TAU_LOCAL_PROVIDER`, `TAU_LOCAL_MODEL`, and `TAU_LOCAL_THINKING`, and a model selected then is used by the first turn. |
| Mock chat | Supported. |
| Chat session list/history | Supported through `/api/local/v1/chat-sessions`; history uses the platform's projected-history shape, with text, reasoning, tool calls, and the running turn in `inProgressMessage`, for the authenticated process principal's sessions only. |
| Local Agent identity | Supported through `/api/local/v1/agent`, from the workspace's `.agents/agent_card.json`. |
| Agent inspection and project-tool workbench | Local mode only, under `/api/local/v1/sessions/{sessionUid}/`; managed mode returns 409. |
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

`configuration.returnImmediately=false` uses a bounded server wait:
`MAINSEQUENCE_TAU_A2A_TASK_WAIT_TIMEOUT_SECONDS`, never past the platform's Message limit when the
platform sets one. Reaching that bound returns the latest non-terminal Task handle and leaves
execution running. Caller disconnect and transport timeout never imply Task cancellation.

### Task conversation, result, and execution correlation

A Task has four separate durable projections: ordered Message history, current/final Artifacts,
Tau execution entries, and technical Task events/logs. Public `Task.history` contains only complete
A2A Messages. A completed Artifact is not duplicated as a responder Message, and streaming
Artifact revisions are not additional Messages. Artifacts, Tau entries, Task events, logs,
prompts, and tool traffic are not history.

`configuration.historyLength` applies to REST/JSON-RPC Message send and stream operations;
`historyLength` applies to Task get and list. Omission requests the SDK's bounded default tail of
100 Messages, zero performs no history-tail read and omits `history`, and a positive integer returns
at most that many latest Messages ordered oldest-to-newest. Values above 100 are capped; booleans,
negative values, and non-integers are rejected.

The Main Sequence storage binding persists requester/responder direction and maps it to public A2A
v1 `ROLE_USER`/`ROLE_AGENT` only at the protocol boundary, without changing Message identity,
Parts, metadata, extension URI order, or Task references. Status Messages have stable identities,
non-empty Parts, matching Task/context IDs, and URI-array extensions whose payload is stored under
Message metadata. A status event references that durable Message; stream projection reloads the
coherent Task instead of trusting an embedded event copy. Status settlement accepts only a
complete responder Message or no Message; the pre-cutover `{code, message}` status object is not
supported.

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
