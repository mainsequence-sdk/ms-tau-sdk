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

Sessionless execution:

```text
agent-targeted response request
  -> provider credential hydration
  -> direct Tau AgentHarness
  -> one response (the SSE form currently emits the completed result)
```

Local development execution:

```text
chat request with an optional local session uid
  -> user-JWT-authenticated provider evidence and credential hydration
  -> workspace-bound Tau CodingSession with live Main Sequence MCP
  -> SQLite entries, leases, activity, cancellation, and snapshots
  -> no Agent/AgentSession/task persistence calls
```

Local state defaults to `~/.tau/mainsequence/<workspace-hash>/runtime.sqlite3`. It is never
uploaded when local mode is disabled. Main Sequence authentication, provider hydration, model
inference, and MCP remain remote; MCP side effects are real platform side effects.

## Operations

The executable operation contract covers:

- `/health`, `/ready`, and `/version`;
- `/api/chat`, mock chat, session model, and cancellation;
- agent-targeted response and response-stream routes;
- A2A message send/stream, task list/get/cancel/subscribe, push-notification compatibility routes,
  and JSON-RPC; and
- controlled internal dispatch and caller-delivery availability routes.

The exact methods and paths are frozen in `tests/contract/test_http_surface.py`. Wire examples and
schema behavior are tested rather than duplicated manually here.

### Local-mode route boundary

| Surface | Local behavior |
| --- | --- |
| Health, readiness, version | Supported; reports local mode and dependency readiness. |
| Chat stream | Supported; `sessionUid` may be omitted. |
| Session model and cancellation | Supported for an existing local session. |
| Mock chat | Supported. |
| Agent-targeted responses | `local_mode_capability_unsupported`. |
| A2A, task dispatch, caller delivery, discovery | `local_mode_capability_unsupported`. |

The unsupported surfaces require registered platform identity or task coordination. Local mode
does not create hidden platform records to satisfy them.

## Effective composition diagnostics

Health reports safe process state: session counts, readiness, MCP catalog counts, snapshot counts,
project-extension counts and errors, and the effective tool-catalog digest. It never reports
credential values or prompt content.

## Failures

SDK errors use a stable JSON envelope with `ok=false`, an error code, message, and safe detail.
Backend status, lease conflicts, busy sessions, missing sessions, cancellation, output limits, and
invalid strict-JSON results retain distinct behavior covered by contract tests.
