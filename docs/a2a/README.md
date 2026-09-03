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

For outbound message delivery from a Tau session, Astro injects the constrained
`mainsequence__a2a_send_message` host tool. The tool uses Django MCP to inspect the target Agent,
create or reuse its backend-owned child `AgentSession`, and resolve a fresh direct-runtime access
bundle. It then sends `message:send` to the exact Tau A2A path returned by Django. The model never
supplies a runtime URL or bearer token, and the token is not included in the tool result. Generic
shell and content-fetch tools are not the outbound A2A transport.

## Outbound host-tool contract

After selecting an Agent from fresh bounded discovery, the model calls the host tool with
orchestration arguments. For a new target conversation:

```json
{
  "agent_uid": "<selected-Agent.uid>",
  "handle_unique_id": "<stable-task-handle>",
  "message": "<bounded request>",
  "message_id": "<stable-message-id>"
}
```

For a continuation, `agent_session_uid` replaces `handle_unique_id`. The required
`agent_uid` always identifies the selected discovery result. This object is the host-tool input;
it is not sent to the target runtime.

The host tool turns those arguments into the transport request:

```json
{
  "message": {
    "messageId": "<stable-message-id>",
    "role": "ROLE_REQUESTER",
    "contextId": "<target-AgentSession.uid>",
    "parts": [{"text": "<bounded request>"}]
  },
  "configuration": {"responseKind": "message"}
}
```

The returned message must use `ROLE_RESPONDER` and the same target `AgentSession.uid` as its
`contextId`. Target selection and transport construction are therefore separate contracts:
`agent_uid` selects the target for the host tool, while `message.role` carries requester/responder
direction on the runtime wire.

If Django reports a transient runtime interaction (`checking`, `starting`, `waking`, or
`updating`), the host tool re-resolves the same target session only after the backend-provided
`retry_after_ms`. It stops immediately when submission is permitted or the state becomes terminal;
it never retries a terminal action and never derives readiness from `runtime_presence`. This is an
active-call wait, not a permanent runtime poller.

The outbound host tool currently supports the direct `message` result kind. It preserves the
target session UID for continuation and reports its generated message ID when a timeout or
disconnect leaves the delivery outcome ambiguous.

Astro models A2A message authorship directly as the transport directions `requester` and
`responder`. The Main Sequence A2A wire values are `ROLE_REQUESTER` and `ROLE_RESPONDER`; there is
no compatibility translation to identity-oriented role names. These wire values never determine
whether the authenticated principal is a human or an Agent. Principal identity comes only from the
gateway-verified caller headers described below. An Agent calling another Agent is the requester
for that exchange and is still authenticated and recorded as an Agent.

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
