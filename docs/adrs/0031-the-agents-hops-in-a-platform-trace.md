# ADR 0031: The Agent's hops in a platform trace


Date: 2026-11-26

Status: Accepted on 2026-11-26, by the owner's instruction to implement it. Implemented in the SDK
on 2026-11-26 for 3.0.4, with the changes that [Implementation](#implementation) records.

Related: ADR 0013 (failure observability), ADR 0014 (attempt-to-turn
correlation), ADR 0027 (hydrate endpoints carry no context), ADR 0028 (session
subowners, which reads the caller context's baggage), [runtime contract: Caller
context](../reference/runtime-contract.md#caller-context).

## Context

The platform's side:

- **A trace is a set of log events.** The platform's runtime log events that
  share one W3C trace id form a trace. Each boundary's terminal event is its
  span, with its `trace_id`, `span_id`, `parent_span_id` and `duration_ms`.
- **Where the events are kept.** The platform keeps them with the runtime
  logs, in the cloud account that ran the Agent. It reads them through its log
  routes with an exact `trace_id` filter, scoped to what the caller may see.
- **No export.** The platform exports no spans over OTLP and sets no `OTEL_*`
  variable.
- **Task dispatch.** A Task's dispatch signal carries the `traceparent` and
  `baggage` stored with the Task.

What Tau does today, read from source at `15574ac`:

- **Request span.** `RequestContextMiddleware` mints a request `span_id`
  (16 hex). It takes `trace_id` and `parent_span_id` from the inbound
  `traceparent`. Without one, it mints a `trace_id` (32 hex) and writes it
  on the terminal `http.request.*` event, but never sends it on.
- **Outbound calls forward the caller's span.** `outbound_headers()`
  injects the caller's `traceparent` unchanged, so the Agent's own hop never
  appears as a parent. Lease renewals, model calls and hydrate calls carry
  nothing, by design.
- **Child events share the request's span.** `agent.run.*`, `agent.turn.*`,
  `agent.model.*`, `agent.tool.*` and handoff events carry their own uids but
  inherit the request's `span_id` from the log context. Only
  `create_background_task` mints a child `span_id` and `parent_span_id`.
- **Backend calls.** Calls to the backend write `dependency.call.completed`.
- **A dead GCP field.** A processor writes `logging.googleapis.com/trace`
  when `GOOGLE_CLOUD_PROJECT` is set and the event holds `otelTraceID`.
  Nothing sets that key, so it never fires.
- **OpenTelemetry use.** Tau depends on `opentelemetry-api` only and has no
  TracerProvider. Tau never depends on `mainsequence` (ADR 0005), so it
  follows the platform's rules on its own.

## Decision

### 1. The request span is the current span

After `RequestContextMiddleware` mints the request `span_id`, it attaches
`SpanContext(trace_id, span_id, is_remote=False)` as the current,
non-recording span.

- **Inbound context.** `request_context()` still attaches the verified
  request's `baggage`, and keeps the inbound `traceparent` only as the
  recorded parent.
- **Outbound calls.** From then on, `outbound_headers()` sends Tau's own span
  as the parent.
- **No inbound `traceparent`.** The minted `trace_id` is propagated with the
  sampled flag `01`.

### 2. Each lifecycle is its own span

Each terminal event gets its own `span_id`, and its `parent_span_id` is the
enclosing span:

| Span | Parent |
| --- | --- |
| Task attempt (`attempt_uid`) | The dispatch signal's `traceparent` |
| Run (`agent_run_uid`) | The request span or the attempt span |
| Turn (`turn_uid`) | The run |
| Model call (`model_call_uid`, one span per attempt) | The turn |
| Tool call (`tool_call_uid`) | The turn |
| Handoff (`handoff_uid`) | The turn |
| Background task | The span that started it (as today) |
| Backend call (`dependency.call.*`) | The enclosing span |

Each span id is sent as the `traceparent` parent of the calls made inside it,
which are backend, MCP and A2A calls. The uids stay what they are. A span id
is a separate 16-hex value, and nothing persisted changes.

### 3. What carries no context stays the same

Model providers, the model relay, hydrate endpoints and lease renewals still
get no `traceparent` or `baggage`. The model call is still a span, recorded
by its own `agent.model.*` terminal event. The MCP library's own spans stay
no-op, because Tau sets no TracerProvider.

### 4. Remove the GCP trace field

The `logging.googleapis.com/trace` processor and the `GOOGLE_CLOUD_PROJECT`
read are removed. They never fire. If they did, they would name a provider
trace that does not exist, in a console no Agent owner can open.

### 5. No new setting, dependency or export

Tau adds no exporter, flush or setting, and keeps `opentelemetry-api` as its
only OpenTelemetry dependency. Its spans are the log lines it already writes
to stdout.

## Consequences

- A trace through an Agent shows the Agent's request, task attempt, run, turn,
  and each model, tool and backend call, under the hop that called it.
- No extra log line is written. Only the ids on existing lines change.

## Documentation

- `docs/reference/runtime-contract.md`: a "Distributed tracing" subsection
  under "Operations", next to "Caller context", with the span table above.
- `docs/reference/security-model.md`, section "Caller context": one paragraph
  saying spans follow the log redaction rules and model calls carry no
  context.
- `CHANGELOG.md`, and the `tau_security_and_access` skill.

## Implementation

Implemented as decided, except where reading the code showed a part could not work as written.
Each change is listed with its reason.

1. **A Task attempt and a background task are no span of their own.** Neither writes a terminal
   line: an attempt logs only a failure, and background work only `runtime.background.failed`. A
   span id minted for them would be a parent that no line describes, and every span inside would
   hang from it. Today the background span was visible only because the `agent.run.*` lines
   inside it carried it; with the run its own span, nothing would. So detached work stays in the
   span that started it: its lines carry that span's `span_id` and `parent_span_id`, and the spans
   inside it are that span's children. A dispatched Task's attempt therefore runs in the dispatch
   request's span, whose parent is the dispatch signal's `traceparent`, and its run is a child of
   that request. The `attempt_uid` stays on the attempt's lines. A Task attempt span would need a
   new terminal line, which this ADR rules out.
2. **Model call lines gain `duration_ms`.** `agent.model.completed` and `.failed` gave their
   duration only as `model_duration_ms`, and a span's duration is `duration_ms`. Both now carry
   it, with the same value. This adds a field to existing lines, and no line.
3. **Handoffs and MCP calls.** A handoff's span is a child of the span it starts in, the turn, and
   is never the current span: handoff events may overlap, and no runtime code runs between them
   outside a tool call. An MCP call writes no `dependency.call.*` line, so it is no span of its
   own and names the tool call's span. Platform calls and application calls through
   `platform_client()` write `dependency.call.*` lines and are each a span, one per attempt.
4. **The request span everywhere.** The request span is current for every HTTP request, outside
   hosted mode too, where requests carry no baggage. A WebSocket has no request span, so
   `request_context()` still makes its `traceparent` the trace parent. A span that
   instrumentation records stays current, and the runtime's log ids stay its own.
5. **A trace the runtime starts says so.** Its terminal line now says `trace_sampled: true`, to
   match the sampled flag `01` it is sent on with. A caller's flag and `tracestate` are kept.

The span table, with each span's terminal line, is in the runtime contract's
[Distributed tracing](../reference/runtime-contract.md#distributed-tracing) section.

