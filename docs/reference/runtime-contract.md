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
  -> workspace-bound Tau CodingSession with live Main Sequence MCP
  -> SQLite entries, leases, snapshots, and A2A Task/message/artifact/event state
  -> no Agent/AgentSession or platform task-persistence calls
```

Local state defaults to `~/.tau/mainsequence/<workspace-hash>/runtime.sqlite3`. It is never
uploaded when local mode is disabled. Main Sequence authentication, provider hydration, model
inference, and MCP remain remote; MCP side effects are real platform side effects.

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
| Outbound A2A through MCP | Message and polled Task flows use authenticated-user semantics. |
| Internal backend dispatch/caller delivery | `local_mode_capability_unsupported`. |
| Platform discovery, push notifications, `resume_caller` | Unsupported without explicit platform registration/callback support. |

An A2A protocol Task does not require a platform AgentSession. Only the remaining unsupported
surfaces require registered platform routing or callback identity, and local mode does not create
hidden records to satisfy them. Incoming local Message and Task calls do not require managed
gateway `X-Caller-*` headers: the runtime records workspace-local provenance, canonicalizes each
caller-supplied `contextId` into the workspace session namespace, and preserves the caller's public
`taskId`. Local Task rows, messages, artifacts, attempts, and event sequences survive process
restart. Outbound MCP Task workflows use `poll`; `resume_caller` is rejected in local mode.

## Effective composition diagnostics

Health reports safe process state: session counts, readiness, MCP catalog counts, snapshot counts,
project-extension counts and errors, and the effective tool-catalog digest. It never reports
credential values or prompt content.

## Failures

SDK errors use a stable JSON envelope with `ok=false`, an error code, message, and safe detail.
Backend status, lease conflicts, busy sessions, missing sessions, cancellation, output limits, and
invalid strict-JSON results retain distinct behavior covered by contract tests.
