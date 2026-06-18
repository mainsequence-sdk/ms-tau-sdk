# ADR 38: Dual-Sink Logging Contract

Status: Accepted
Date: 2026-06-18
Implementation Status: Partially implemented

Initial implementation is complete for the TypeScript stream runtime, A2A runtime path, HTTP access
logs, checkpoint sidecar, provider signin helper, and session-model extension hook. Standalone
developer/bootstrap scripts that intentionally print interactive progress are still outside the
central logger unless noted below.

## Context

Astro logs are currently hard to debug because multiple independent formats are written directly to
stdout/stderr:

- structured JSON lifecycle events
- ad hoc `[astro-stream] ...` text lines
- ad hoc `[astro-http] ...` access lines
- raw Pi stream chunks
- raw tool outputs
- large escaped JSON payloads
- multiline child-process and SDK exception output

This creates two separate failures:

- GKE receives inconsistent log shapes, making scraping, filtering, and alerting unreliable.
- Humans reading local or pod logs see a wall of mixed JSON, raw stream chunks, huge payloads, and
  escaped stack traces.

This is not only a debug-level problem. It affects every severity and every log source:

- `DEBUG`
- `INFO`
- `WARNING`
- `ERROR`
- HTTP access logs
- runtime lifecycle logs
- A2A message and runtime attach logs
- backend fetch/auth logs
- checkpoint logs
- Pi child process logs
- tool-call/tool-result logs
- exception logs
- startup/shutdown logs

The current model tries to make one stdout stream serve both machines and humans. It fails at both.

## Problem

Astro needs structured logs so GKE can ingest them correctly, but the terminal view must not be the
same giant JSON stream.

What gets sent to machine logging and what gets printed for a human must be different renderings of
the same internal event.

Current direct logging has these concrete problems:

- request timelines cannot be reconstructed cleanly because events do not consistently carry a
  request id
- overlapping `/runtime`, `/message:send`, backend fetches, warm-runner events, and retries are hard
  to distinguish
- raw tool results and prompt/system files can flood the terminal
- raw SSE chunks expose internal runtime events instead of useful summaries
- stack traces are sometimes embedded as huge escaped strings inside JSON
- access logs use a different format than structured app events
- child process stdout/stderr can bypass the structured logger entirely

## Decision

Astro will adopt a global dual-sink logging contract.

Application code must emit one structured internal log event. A central logger then renders that
event to one or more configured sinks:

```text
structured log event
  -> machine sink: JSON event for GKE / log aggregation
  -> human sink: compact terminal summary
  -> payload sink: optional raw large payloads when explicitly enabled
```

The human terminal output is a first-class product requirement. It is not a debug-only convenience.

The machine sink and human sink must have different rendering rules:

- machine sink receives complete structured metadata suitable for indexing
- human sink receives compact one-line summaries suitable for reading
- payload sink receives raw large data only when explicitly enabled

No application code should write user-facing operational logs directly with `console.log`,
`console.warn`, or `console.error`. Direct writes are allowed only inside the logger implementation
or for process-level fatal fallback when the logger cannot be initialized.

## Log Event Model

Every log event should have a normalized internal shape:

```ts
type AstroLogEvent = {
  severity: "DEBUG" | "INFO" | "WARNING" | "ERROR";
  event: string;
  component: string;
  message?: string;
  time?: string;
  requestId?: string;
  traceId?: string;
  sessionId?: string;
  agentSessionUid?: string;
  agentType?: string;
  route?: string;
  method?: string;
  statusCode?: number;
  durationMs?: number;
  phase?: string;
  outcome?: "success" | "failure" | "skipped" | "canceled";
  errorCode?: string;
  errorType?: string;
  errorMessage?: string;
  stackHash?: string;
  data?: Record<string, unknown>;
};
```

Rules:

- `event` is required and must be stable, for example `a2a.message.completed`.
- `component` is required and must be stable, for example `astro-stream`.
- `requestId` is required for every HTTP request lifecycle event and must be propagated into nested
  runtime, backend fetch, checkpoint, and warm-runner events.
- `durationMs` must be present for completed work units.
- Errors must expose `errorCode`, `errorType`, `errorMessage`, and `phase` when available.
- Large values must not be placed directly in `data` by default.

## Machine Sink

The machine sink emits one JSON object per line.

This is the only sink intended for GKE ingestion.

Example:

```json
{"severity":"INFO","time":"2026-06-18T12:17:52.018Z","component":"astro-stream","event":"a2a.message.completed","request_id":"req_01","session_id":"0b2701a1-e777-4cfe-8437-b94025f00069","agent_session_uid":"0b2701a1-e777-4cfe-8437-b94025f00069","agent_type":"astro-orchestrator","route":"/api/a2a/v1/message:send","status_code":200,"duration_ms":842,"runner":"warm","bootstrap_reused":true}
```

Rules:

- Use snake_case field names at the machine boundary.
- Emit valid single-line JSON only.
- Do not mix `[astro-stream]` or `[astro-http]` text into the machine sink.
- Do not emit raw SSE chunks, raw tool payloads, prompts, full backend responses, or full stack
  traces by default.
- Include payload references or hashes instead of full payloads.

## Human Terminal Sink

The human terminal sink renders the same internal events as short one-line summaries.

Example:

```text
12:17:52 INFO  a2a.message.completed session=0b270... status=200 842ms runner=warm
12:17:53 WARN  a2a.bootstrap.reused session=0b270... reason=already-ready
12:17:54 ERROR backend.session.fetch.failed session=0b270... phase=auth_headers 120000ms timeout
```

Rules:

- One line per event.
- Never print raw JSON blobs.
- Never print full tool results.
- Never print raw SSE chunks.
- Never print full prompt/system/skill files.
- Never print escaped multiline stack traces.
- Show stable short event names.
- Show the most important identifiers in shortened form by default.
- Always show duration when available.
- Always show status, phase, and error code for failures when available.
- Summarize long values as `bytes=<n>`, `items=<n>`, `preview="..."`, `hash=<hash>`, or
  `payload=<ref>`.
- Color is allowed for local TTY output, but the format must remain readable without color.

The human sink is for local terminals and interactive development. It must not be mixed into the GKE
machine sink.

## Payload Sink

Raw large payloads are not normal logs.

Examples:

- tool results
- full prompt/system text
- raw model responses
- raw backend response bodies
- full stack traces
- full SSE streams
- checkpoint bundle fragments

These may be written only when explicitly enabled through a payload sink.

Payload events should use references:

```json
{
  "severity": "DEBUG",
  "event": "tool.result.recorded",
  "tool_name": "read",
  "bytes": 12054,
  "preview": "---\\nname: a2a_communication...",
  "payload_ref": "payload://req_01/tool_04"
}
```

Human rendering:

```text
12:17:52 DEBUG tool.result.recorded tool=read bytes=12054 payload=payload://req_01/tool_04
```

## Configuration

Astro should support explicit logging configuration:

```env
ASTRO_LOG_MACHINE_SINK=json
ASTRO_LOG_HUMAN_SINK=pretty
ASTRO_LOG_LEVEL=info
ASTRO_LOG_PAYLOADS=0
ASTRO_LOG_STREAM_CHUNKS=0
ASTRO_LOG_STACK_MODE=summary
ASTRO_LOG_REQUEST_TIMELINE=1
```

Recommended defaults:

Local Docker/dev:

```env
ASTRO_LOG_MACHINE_SINK=off
ASTRO_LOG_HUMAN_SINK=pretty
ASTRO_LOG_LEVEL=info
ASTRO_LOG_PAYLOADS=0
ASTRO_LOG_STREAM_CHUNKS=0
ASTRO_LOG_STACK_MODE=summary
ASTRO_LOG_REQUEST_TIMELINE=1
```

GKE:

```env
ASTRO_LOG_MACHINE_SINK=json
ASTRO_LOG_HUMAN_SINK=off
ASTRO_LOG_LEVEL=info
ASTRO_LOG_PAYLOADS=0
ASTRO_LOG_STREAM_CHUNKS=0
ASTRO_LOG_STACK_MODE=summary
ASTRO_LOG_REQUEST_TIMELINE=1
```

If both machine and human sinks are enabled at the same time, they must write to separate configured
targets so GKE does not ingest pretty terminal output as machine logs.

## Request Timeline

Every HTTP request should produce a compact request timeline using the same `requestId`.

Example machine events:

```json
{"event":"request.started","request_id":"req_01","route":"/api/a2a/v1/message:send","method":"POST"}
{"event":"backend.session.fetch.completed","request_id":"req_01","duration_ms":712,"status_code":200}
{"event":"a2a.bootstrap.reused","request_id":"req_01","session_id":"0b270...","warm_runner_ready":true}
{"event":"runtime.turn.started","request_id":"req_01","session_id":"0b270..."}
{"event":"runtime.turn.completed","request_id":"req_01","duration_ms":930}
{"event":"request.completed","request_id":"req_01","status_code":200,"duration_ms":1702}
```

Example human rendering:

```text
12:17:51 INFO  request.started req=req_01 POST /api/a2a/v1/message:send
12:17:51 INFO  backend.session.fetch.completed req=req_01 status=200 712ms
12:17:51 INFO  a2a.bootstrap.reused req=req_01 session=0b270... runner=ready
12:17:52 INFO  runtime.turn.completed req=req_01 session=0b270... 930ms
12:17:52 INFO  request.completed req=req_01 status=200 1702ms
```

## Access Logs

HTTP access logs must become normal structured events.

Current text format:

```text
[astro-http] 89.144.221.254 - - [18/Jun/2026:12:17:35 +0000] "POST /api/a2a/sessions/.../runtime HTTP/1.1" 200 - "-" "python-httpx/0.28.1" rt=7353.0ms
```

Target event:

```json
{
  "severity": "INFO",
  "event": "http.request.completed",
  "component": "astro-http",
  "request_id": "req_01",
  "method": "POST",
  "route": "/api/a2a/sessions/{agent_session_uid}/runtime",
  "path": "/api/a2a/sessions/0b2701a1-e777-4cfe-8437-b94025f00069/runtime",
  "status_code": 200,
  "duration_ms": 7353,
  "remote_address": "89.144.221.254",
  "user_agent": "python-httpx/0.28.1"
}
```

Human rendering:

```text
12:17:35 INFO  http.request.completed req=req_01 POST /api/a2a/sessions/{uid}/runtime status=200 7353ms
```

## Stream And Tool Logs

Pi stream output must not be logged as raw client-visible chunks by default.

Replace logs like:

```text
[astro-stream] OUT agent_type=... tool-result result="{...large payload...}"
```

with summary events:

```text
12:17:52 DEBUG pi.chunk tool_result session=0b270... tool=read bytes=12054 payload=payload://req_01/tool_04
```

Machine event:

```json
{"severity":"DEBUG","event":"pi.chunk","chunk_type":"tool_result","session_id":"0b270...","tool_name":"read","bytes":12054,"payload_ref":"payload://req_01/tool_04"}
```

## Error And Stack Trace Policy

Errors must be summarized in normal logs.

Normal machine event:

```json
{
  "severity": "ERROR",
  "event": "backend.auth_headers.failed",
  "request_id": "req_01",
  "phase": "runtime_credential_auth",
  "error_type": "ReadTimeout",
  "error_message": "HTTPSConnectionPool(host='dev-tsorm.ngrok.app', port=443): Read timed out.",
  "stack_hash": "sha256:..."
}
```

Human rendering:

```text
12:17:54 ERROR backend.auth_headers.failed req=req_01 phase=runtime_credential_auth type=ReadTimeout timeout
```

Full stack traces require payload logging or `ASTRO_LOG_STACK_MODE=full`.

## Consequences

Positive:

- GKE receives valid structured logs with stable fields.
- Local terminal logs become readable.
- Request timelines are debuggable.
- Large payloads stop flooding normal logs.
- All severities and components share one logging contract.
- A2A/runtime bugs become easier to trace because `/runtime`, `message:send`, backend fetch,
  warm-runner, checkpoint, and Pi events share one `requestId`.

Negative:

- Requires replacing many direct `console.log` and `console.error` call sites.
- Requires normalizing child-process stdout/stderr.
- Requires careful defaults so local and GKE deployments do not accidentally mix sinks.
- Requires a migration period where some legacy logs may still exist.

## Implementation Tasks

- [x] Add a central logger module with structured internal event objects.
- [x] Add machine JSON renderer with one valid JSON object per line.
- [x] Add human pretty renderer with compact one-line summaries.
- [ ] Add optional payload sink for raw large payloads.
- [x] Add request id generation at HTTP ingress.
- [ ] Propagate `requestId` through runtime context, backend fetches, checkpoint operations,
  warm-runner operations, A2A message handling, and child-process events.
- [x] Replace `[astro-http]` access logs with `http.request.completed` structured events.
- [x] Replace `[astro-stream] OUT ...` stream logs with summarized `pi.chunk` events.
- [ ] Replace direct application `console.log`, `console.warn`, and `console.error` calls with the
  logger, except inside the logger implementation and process-fatal fallback paths.
- [x] Normalize TypeScript runtime child-process stdout/stderr into logger events.
- [x] Add payload size limits and preview/hash helpers.
- [x] Summarize errors and stack traces by default.
- [x] Add `ASTRO_LOG_MACHINE_SINK`, `ASTRO_LOG_HUMAN_SINK`, `ASTRO_LOG_LEVEL`,
  `ASTRO_LOG_PAYLOADS`, and `ASTRO_LOG_STACK_MODE`.
- [ ] Add active behavior for `ASTRO_LOG_STREAM_CHUNKS` and `ASTRO_LOG_REQUEST_TIMELINE`.
- [x] Configure local Docker/dev defaults for human pretty logs.
- [x] Configure GKE defaults for machine JSON logs only.
- [x] Document how to enable payload logging safely during local debugging.
- [x] Add tests for JSON renderer output, human renderer output, and payload
  redaction/summarization.
- [ ] Add tests for request id propagation across request lifecycle events.
- [ ] Audit all current log call sites and track remaining legacy direct writes.

Remaining known direct writes:

- `scripts/start_pi.mjs` is a standalone local bootstrap script run directly by Node. It still emits
  interactive setup progress with `[astro]` prefixes.
- `scripts/patch_pi_rpc_ready.mjs` is a local maintenance script and still prints direct progress.
- `scripts/debug_a2a_endpoint.mjs` is an interactive debug client and intentionally prints request
  and response JSON for humans.

## Out Of Scope

- Changing runtime behavior, A2A protocol behavior, checkpoint behavior, or backend auth behavior.
- Replacing the external GKE logging stack.
- Logging full prompts, tool results, or model responses by default.
