# A2A

Astro implements the standard A2A surface directly in FastAPI:

- `POST /api/a2a/v1/message:send`
- `POST /api/a2a/v1/message:stream`
- `GET /api/a2a/v1/tasks`
- `GET /api/a2a/v1/tasks/{task_id}`
- `POST /api/a2a/v1/tasks/{task_id}:cancel`
- `GET /api/a2a/v1/tasks/{task_id}:subscribe`
- task push-notification configuration routes, which currently return the standard unsupported
  response
- `GET /api/a2a/v1/extendedAgentCard`
- `POST /api/a2a/rpc`

`message.contextId` is the backend `AgentSession.uid`. Astro loads that Tau
session, acquires its runtime lease, and persists task/message/status state in
the Django `AgentTask` models only when the caller selects a Task result.

`configuration.responseKind="message"` executes directly and returns an A2A
agent `Message` without creating an `AgentTask`.
`configuration.responseKind="task"` returns a durable `Task` and executes it
as tracked background work that drains or cancels during ASGI shutdown. The
field activates the versioned response-kind extension through the
`A2A-Extensions` header; omission defaults to direct Message execution.
Streaming and subscription responses use SSE. Non-strict streaming emits
incremental artifact updates with direct backpressure before the final durable
task event. Strict JSON output is bounded and validated before its artifact is
emitted.

JSON-RPC accepts both the named methods and standard slash forms, including
`SendMessage`/`message/send`, `SendStreamingMessage`/`message/stream`,
`GetTask`/`tasks/get`, `ListTasks`/`tasks/list`, and
`CancelTask`/`tasks/cancel`. Streaming JSON-RPC responses are SSE frames whose
payloads retain the original JSON-RPC request id.

Push notifications are deliberately disabled until the backend provides canonical durable
configuration storage and webhook delivery. Agent Cards report `pushNotifications: false`.
REST push-configuration routes return HTTP `400` with
`PUSH_NOTIFICATION_NOT_SUPPORTED`; current and legacy JSON-RPC push methods return code `-32003`.
Astro does not contact the backend for these unsupported operations.

Input supports text parts and standard inline PDF `Part.raw` payloads. URL file
parts and non-PDF raw parts are rejected. Inline files are size-limited,
validated by signature, and written with mode `0600`.

The removed runtime-attach routes under `/api/a2a/sessions/.../runtime` are not
supported.

`GET /api/a2a/v1/extendedAgentCard` requires `agent_session_uid`,
`session_uid`, or `contextId`. The response is loaded from Django's persisted
agent card rather than from a static Astro definition.

## Caller identity

`message:send`, `message:stream`, the JSON-RPC message methods, and task
turns require the gateway-verified caller identity headers described in
[request.md](../interface/request.md#caller-identity-headers). An A2A caller
is identified only by those headers; `metadata`, `message.metadata`, and the
Pi envelope are never an identity source. A missing or invalid set is
rejected before any session work with HTTP `403` on the REST routes and a
JSON-RPC error (code `-32000`, `data.code` `runtime_caller_identity_invalid`)
on `/api/a2a/rpc`. Task read, cancel, and subscribe methods do not require the
headers.
