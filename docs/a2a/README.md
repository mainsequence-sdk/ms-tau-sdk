# A2A

Astro implements the standard A2A surface directly in FastAPI:

- `POST /api/a2a/v1/message:send`
- `POST /api/a2a/v1/message:stream`
- `GET /api/a2a/v1/tasks`
- `GET /api/a2a/v1/tasks/{task_id}`
- `POST /api/a2a/v1/tasks/{task_id}:cancel`
- `GET /api/a2a/v1/tasks/{task_id}:subscribe`
- task push-notification configuration routes
- `GET /api/a2a/v1/extendedAgentCard`
- `POST /api/a2a/rpc`

`message.contextId` is the backend `AgentSession.uid`. Astro loads that Tau
session, acquires its runtime lease, and persists task/message/status state in
the Django `AgentTask` models.

Synchronous sends return an A2A agent `Message`. Requests with
`configuration.returnImmediately=true` return a durable `Task` and execute it
as tracked background work that drains or cancels during ASGI shutdown.
Streaming and subscription responses use SSE. Non-strict streaming emits
incremental artifact updates with direct backpressure before the final durable
task event. Strict JSON output is bounded and validated before its artifact is
emitted.

JSON-RPC accepts both the named methods and standard slash forms, including
`SendMessage`/`message/send`, `SendStreamingMessage`/`message/stream`,
`GetTask`/`tasks/get`, `ListTasks`/`tasks/list`, and
`CancelTask`/`tasks/cancel`. Streaming JSON-RPC responses are SSE frames whose
payloads retain the original JSON-RPC request id.

Input supports text parts and standard inline PDF `Part.raw` payloads. URL file
parts and non-PDF raw parts are rejected. Inline files are size-limited,
validated by signature, and written with mode `0600`.

The removed runtime-attach routes under `/api/a2a/sessions/.../runtime` are not
supported.

`GET /api/a2a/v1/extendedAgentCard` requires `agent_session_uid`,
`session_uid`, or `contextId`. The response is loaded from Django's persisted
agent card rather than from a static Astro definition.
