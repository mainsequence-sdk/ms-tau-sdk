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

For outbound message delivery from a Tau session, Astro's generic MCP projection exposes Django's
canonical `a2a.send_message` operation to the model as the constrained
`mainsequence__a2a_send_message` host tool. Astro has no separate outbound A2A tool implementation.
Django inspects the target Agent, creates or reuses its backend-owned child `AgentSession`, resolves
fresh runtime access, constructs the standard envelope, sends `message:send`, validates the
response, and records the send audit event. The model never supplies a caller session, runtime URL,
bearer token, or wire role, and none is included in the tool result. Generic shell and content-fetch
tools are not the outbound A2A transport.

## Outbound host-tool contract

After selecting an Agent from fresh bounded discovery, the model calls the host tool with
orchestration arguments. For a new target conversation:

```json
{
  "agent_uid": "<selected-Agent.uid>",
  "handle_unique_id": "<stable-task-handle>",
  "message": "<bounded request>",
  "message_id": "<stable-message-id>",
  "response_kind": "message"
}
```

For a continuation, `agent_session_uid` replaces `handle_unique_id`. The required
`agent_uid` always identifies the selected discovery result. This object is the host-tool input;
it is not sent to the target runtime.

`response_kind` is always required and has no controlled-client default. A Task request also
requires `completion_policy: "poll" | "resume_caller"`; the field is invalid for Message mode.
The generic projection also exposes Django's `a2a.get_task`, `a2a.cancel_task`, and
`a2a.wait_task` operations when they are present in the MCP catalog.

The MCP catalog marks every protected operation with Tool `_meta`
`mainsequence.ai/requires-caller-session-proof/v1: true`. Astro's generic MCP projection reads
that marker and privately adds this caller-session proof to MCP `tools/call` `_meta`:

```json
{
  "mainsequence.ai/caller-session-proof/v1": {
    "caller_agent_session_uid": "<active-caller-AgentSession.uid>",
    "lease_holder_id": "<active-runtime-holder>",
    "lease_token": "<active-runtime-lease-token>"
  }
}
```

These values come from the runtime host after it acquires the caller session's lease; they are not
part of the model-visible tool schema. Django binds the proof to the authenticated coding-agent
service, its Agent and Environment, and the exact unexpired `runtime_run` lease. It also requires
the target child session's immediate parent to be that exact caller session, including
continuations. The same marker-driven injection protects `agent.update_runtime`; Astro does not
maintain a tool-name allowlist. If a marked tool is projected without host proof, session setup
fails instead of exposing a tool that can only return a misleading 403.

Django turns the semantic arguments into the transport request:

```json
{
  "message": {
    "messageId": "<stable-message-id>",
    "role": "ROLE_REQUESTER",
    "contextId": "<target-AgentSession.uid>",
    "parts": [{"text": "<bounded request>"}]
  },
  "configuration": {"responseKind": "<message-or-task>"}
}
```

The returned message must use `ROLE_RESPONDER` and the same target `AgentSession.uid` as its
`contextId`. Target selection and transport construction are therefore separate contracts:
`agent_uid` selects the target for the host tool, while `message.role` carries requester/responder
direction on the runtime wire.

Django follows only backend-declared transient runtime-interaction states within a bounded
readiness window and requires `runtime_interaction.can_submit=true` before delivery. When
submission remains blocked, the tool returns the backend-owned notice; neither Astro nor the model
derives readiness from `runtime_presence`.

The outbound host tool preserves the selected Message-or-Task result shape. Controlled Task sends
use `returnImmediately: true`: `poll` leaves completion to explicit get/wait calls, while
`resume_caller` registers a durable delivery that resumes the proven caller AgentSession on a
terminal or actionable interrupted state.

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
`configuration.responseKind="task"` returns a durable `Task`. Standard
`configuration.returnImmediately=true` returns after the durable Task/dispatch is committed;
`false` or omission waits for a terminal or interrupted Task state. For Message mode,
`returnImmediately` has no effect. The response-kind field activates the versioned extension
through the `A2A-Extensions` header; public omission defaults to direct Message execution.

Task execution first holds the existing AgentSession runtime lease and atomically claims the
durable dispatch attempt. Every output, Message, and settlement refreshes that canonical lease and
carries the current attempt UID as a state fence. A local asyncio task is only an accelerator after
that claim; shutdown does not translate process exit into protocol cancellation. Django's durable
dispatch/recovery contract remains the recovery owner.

Streaming and subscription responses use SSE. Non-strict streaming emits request-local artifact
deltas and persists coalesced durable artifact events. Strict JSON is bounded and validated before
durable publication. REST and JSON-RPC Task subscriptions share the ordered backend event cursor;
reconnects may supply `afterSequence`. Subscription of an already-terminal Task returns the A2A
unsupported-operation error and the client uses GetTask instead.

JSON-RPC accepts both the named methods and standard slash forms, including
`SendMessage`/`message/send`, `SendStreamingMessage`/`message/stream`,
`GetTask`/`tasks/get`, `ListTasks`/`tasks/list`, and
`CancelTask`/`tasks/cancel`, and `SubscribeToTask`/`tasks/subscribe`. Streaming JSON-RPC responses are SSE frames whose
payloads retain the original JSON-RPC request id.

After the existing runtime wake/readiness operation succeeds, the backend sends a bounded internal
dispatch-available signal. Astro pulls the referenced durable Task, acquires its session lease, and
claims the dispatch before scheduling execution. Caller-delivery signals follow the same pattern;
if the caller already has an active turn, the durable delivery remains queued. These internal
control-plane signals are not public A2A push notifications.

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
