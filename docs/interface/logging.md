# Logging

Astro uses the same Structlog pipeline as `tdag-django`. Application code emits
native Structlog events, while logs from Uvicorn, HTTPX, Tau, and other standard
Python loggers pass through the same `ProcessorFormatter`.

Every machine event includes the common backend fields:

- `event_id`
- `event`
- `logger`
- `level`
- `severity`
- `timestamp`
- `request_id`
- `pathname`, `filename`, `module`, `lineno`, and `func_name`
- `source`, formatted as a clickable `path:line`
- `component`
- `runtime_instance_uid` and `sdk_version`

Deployment fields such as organization, project/environment, coding-agent
service, deployment run, code/image digest, Knative revision, agent config,
toolset, and model config revisions are added from trusted environment
variables when available.

The sole Environment correlation field is
`organization_environment_uid`. Astro reads only
`MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID`, accepts the trusted
`X-Organization-Environment-UID` gateway header at the outer request boundary,
and reserves the field against route, tool, backend, and application logger
overrides. The superseded project-environment key, environment variable, and
header are not accepted or emitted.

When OpenTelemetry logging fields and `GOOGLE_CLOUD_PROJECT` are available, the
processor adds the Google Cloud trace, span, and sampled fields used by the
Django service.

## Request context

The ASGI request middleware accepts a bounded, syntactically valid
`X-Request-ID` from the platform gateway or creates a UUID. It binds that ID,
trace/span IDs, and trusted `X-User-UID` and
`X-Coding-Agent-Service-UID` and
`X-Organization-Environment-UID` values through Structlog contextvars,
exposes the selected request ID as `request.state.request_id`, and returns it
as `X-Request-ID`. Runtime, backend, MCP, and provider logs emitted in the
request inherit the same correlation context.

Each non-probe HTTP request emits exactly one terminal event:

```text
http.request.completed
```

Terminal events include the normalized route template, method/protocol, status
class, duration, first-byte time, safe request/response sizes, streaming and
disconnect state, authentication outcome, and actor/service/session IDs when
known. Successful platform-owned `/health` and `/ready` probes emit no request
logs; probe failures and recoveries emit rate-limited operational events that
retain their canonical Organization Environment, runtime, and service identity.
Cancelled and disconnected requests also produce exactly one terminal event and
retain the same canonical Environment correlation without logging request bodies.
Tool telemetry uses the canonical `approval_outcome` field, preserves safe
target-system and side-effect metadata, and correlates timeout retries without
logging tool arguments, credentials, results, or provider error content.
Production never emits `http.request.started`. Raw paths, query strings, IP
addresses, user agents, and arbitrary headers are not access-log fields.
Cancelled and failed requests use `http.request.cancelled` and
`http.request.failed`.

Standard platform lifecycle events omit middleware implementation callsites
such as `logging.py`, `__call__`, and `send_with_context`. Application-authored
domain logs and separate exception events retain their genuine source fields.

Chat and A2A routes bind `agent_session_uid`; A2A routes also bind safe method,
request, context, task, and message identifiers. Agent run/turn events carry
distinct run and turn IDs. Tau model, registered tool, and handoff events emit
durations, controlled names, outcomes, and provider usage without content.
Model retry and rate-limit events preserve a distinct model-call identifier per
attempt and never include provider response bodies. Handoffs retain one
identifier across their start and terminal events, optional parent/child
session identifiers, and an allowlisted reason.
Backend HTTP dependency calls use normalized semantic paths with status,
attempt, latency, retryability, and outcome.

Background A2A tasks receive a durable `operation_uid`, a new span,
`causation_event_id` and `origin_request_id` links. They do not continue using
the completed HTTP request as their active request context.

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

Credential-bearing field names are redacted recursively, and bearer values in
text are redacted. Error events use bounded error types instead of raw
exception values. Request/response bodies, raw query strings, prompts, prompt
hashes, model output, tool arguments/results, provider headers, MCP payloads,
system prompts, and conversation histories are not logged. Conversation
events retain only byte and item counts.

`ASTRO_LOG_PAYLOADS` remains accepted for deployment compatibility but does not
enable prompt or payload logging. Any future content logging requires a
separate privacy and retention decision.

Request-boundary and Tau-observer telemetry is fail-open: an unavailable log
handler or processor cannot change an HTTP response or interrupt agent work.
