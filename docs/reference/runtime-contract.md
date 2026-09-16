# Runtime and HTTP Contract

`ms-tau` and `create_app` expose the same FastAPI application. Durable requests require an existing
backend session identifier; session allocation is not performed implicitly by chat or A2A routes.

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

## Effective composition diagnostics

Health reports safe process state: session counts, readiness, MCP catalog counts, snapshot counts,
project-extension counts and errors, and the effective tool-catalog digest. It never reports
credential values or prompt content.

## Failures

SDK errors use a stable JSON envelope with `ok=false`, an error code, message, and safe detail.
Backend status, lease conflicts, busy sessions, missing sessions, cancellation, output limits, and
invalid strict-JSON results retain distinct behavior covered by contract tests.
