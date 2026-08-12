# Logging

Astro uses the same Structlog pipeline as `tdag-django`. Application code emits
native Structlog events, while logs from Uvicorn, HTTPX, Tau, and other standard
Python loggers pass through the same `ProcessorFormatter`.

Every machine event includes the common backend fields:

- `event`
- `logger`
- `level`
- `severity`
- `timestamp`
- `request_id`
- `pathname`, `filename`, `module`, `lineno`, and `func_name`
- `source`, formatted as a clickable `path:line`
- `component`

When OpenTelemetry logging fields and `GOOGLE_CLOUD_PROJECT` are available, the
processor adds the Google Cloud trace, span, and sampled fields used by the
Django service.

## Request context

The ASGI request middleware creates a 32-character request ID, binds it through
Structlog contextvars, exposes it as `request.state.request_id`, and returns it
as `X-Request-ID`. Runtime, backend, MCP, and provider logs emitted in that
request inherit the same ID.

Each HTTP request emits:

```text
http.request.started
http.request.completed
```

Completion events include `http_method`, `http_path`, route, status, duration,
response size, remote address, and user agent. Chat completion events also
include the session identifier and safe conversation metadata described below.
Cancelled and failed requests use `http.request.cancelled` and
`http.request.failed`.

## Rendering

Production uses single-line JSON on stdout:

```dotenv
ASTRO_LOG_MACHINE_SINK=true
ASTRO_LOG_HUMAN_SINK=false
ASTRO_LOG_LEVEL=INFO
```

Local development uses Django-style, one-line console rendering:

```dotenv
ASTRO_LOG_MACHINE_SINK=false
ASTRO_LOG_HUMAN_SINK=true
ASTRO_LOG_LEVEL=INFO
```

If both sinks are enabled, JSON remains on stdout and console output remains on
stderr.

## Sensitive data

Fields whose names contain `authorization`, `credential`, `password`,
`secret`, or `token` are redacted recursively. Bearer values embedded in event
or exception text are also redacted. Tool results, provider headers, MCP
payloads, system prompts, and complete conversation histories are not logged.

`runtime.turn.received`, `llm.turn.started`, and completed `/api/chat` access
events always include the latest user prompt's character count and SHA-256
fingerprint. When
`ASTRO_LOG_PAYLOADS=true`, they also include `prompt_excerpt`: a
whitespace-normalized preview limited to 200 characters. The default remains
`false`.
