# ADR 37: A2A Standard Wire Protocol

Status: Proposed
Date: 2026-06-17
Implementation Status: Partially implemented

## Context

Astro previously exposed endpoints and payloads that we called "A2A", but the public wire shape was
Astro-specific: top-level convenience fields, runtime-control identity fields, and Pi/UI stream
chunks such as `start`, `text-delta`, `tool-result`, and `finish`.

That shape was useful for local runtime debugging, but it is not the A2A protocol surface.

The current A2A specification defines a binding-independent data model around `Message`, `Task`,
`Part`, `Artifact`, and `AgentCard`, then maps those objects onto protocol bindings:

- JSON-RPC 2.0 over HTTP(S), with PascalCase methods such as `SendMessage` and `GetTask`
- HTTP+JSON/REST, with endpoints such as `POST /message:send`,
  `POST /message:stream`, and `GET /tasks/{id}`
- SSE streaming for task/message updates
- push notifications for long-running tasks

References reviewed for this ADR:

- A2A protocol overview and core concepts:
  https://a2a-protocol.org/latest/specification/#2-terminology
- Send Message and Send Streaming Message semantics:
  https://a2a-protocol.org/latest/specification/#31-core-operations
- JSON-RPC binding:
  https://a2a-protocol.org/latest/specification/#9-json-rpc-protocol-binding
- HTTP+JSON/REST binding:
  https://a2a-protocol.org/latest/specification/#11-httpjsonrest-protocol-binding
- Agent Card discovery:
  https://a2a-protocol.org/latest/specification/#8-agent-discovery-the-agent-card

## Protocol Choice

This ADR chooses the A2A Protocol for the public agent-to-agent wire contract, but it does not
replace Main Sequence's backend model.

The choice is:

- Use A2A Protocol at the public boundary because it directly models agent-to-agent coordination
  with `Message`, `Task`, `Artifact`, streaming updates, task polling, push notifications, and
  Agent Card discovery.
- Do not use MCP as the public A2A contract because MCP primarily models agent-to-tool/resource
  interaction, not autonomous peer-agent task delegation.
- Do not choose AGNTCY Agent Connect or ANP for this immediate standardization pass. They remain
  worth tracking, but the concrete mismatch we need to fix is our current non-standard "A2A"
  request/response shape, and A2A Protocol directly addresses that.

The implementation choice is adapter-based:

- Public clients speak A2A.
- Astro translates A2A objects into Main Sequence backend operations.
- Main Sequence `AgentSession` remains the internal source of truth.

## Problem

Our current A2A shape is wrongly designed as a public protocol because it leaks Main Sequence and
Astro runtime mechanics into the agent-to-agent boundary.

Specific problems:

- It requires backend/runtime identity such as `agent_session_uid` in places where A2A expects an
  agent endpoint plus `Message`, `contextId`, and `taskId` semantics.
- It exposed a runtime attachment model instead of an A2A task/message model.
- It returns Astro stream chunks instead of A2A `Message`, `Task`, `TaskStatusUpdateEvent`, or
  `TaskArtifactUpdateEvent` objects.
- It uses non-standard top-level output controls such as `response_format` and `omit_reasoning`
  instead of A2A `configuration`, `acceptedOutputModes`, `metadata`, and declared extensions.
- It has no first-class public `Task` resource that can be fetched with `GET /tasks/{id}`.
- It has no public Agent Card that advertises the exact supported bindings and base URLs.
- It conflates internal runtime lifecycle with public agent communication.

The recent `omit_reasoning` fixes are therefore only a stopgap. Standard A2A does not stream
internal thoughts, tool calls, checkpoint records, or Pi runner events in the first place. A2A peers
should see agent messages, task state, artifacts, and errors only.

## Decision

Astro should define a standard A2A protocol surface and remove Astro-specific A2A/runtime routes
from the public client contract.

The public A2A surface must follow the A2A specification exactly enough that an A2A client can talk
to Astro without knowing Main Sequence backend sessions, Pi, checkpointing, warm runners, or
our local stream chunk vocabulary.

The target public interface is:

- HTTP+JSON binding under one advertised base URL, proposed as `/api/a2a/v1`
- JSON-RPC binding under one advertised RPC URL, proposed as `/api/a2a/rpc`
- no public `agent_session_uid` route segment for normal message sending
- no public Astro/Pi stream chunks
- no public reasoning/tool/checkpoint trace events

The Agent Card is part of the eventual A2A discovery surface, but it is explicitly deferred from
the first implementation pass. We need to standardize request/response semantics and backend
session mapping before publishing a public discovery document that external clients may cache.

Internal implementation may still use:

- backend `AgentSession`
- runtime bootstrap state
- warm Pi RPC runners
- checkpoint/session state
- provider credential hydration
- Main Sequence user/session authorization

Those are adapter internals, not A2A wire protocol fields.

## Main Sequence Backend Model

`AgentSession` is not legacy. It remains the internal authority for:

- tenant/user authorization
- runtime identity
- agent type
- session continuity
- checkpoint ownership and restore
- provider credential scope
- capability materialization
- project/runtime attachment
- turn serialization

The A2A adapter must not bypass `AgentSession`. It must map A2A `Message`, `contextId`, and
`task.id` into backend-owned session and task records before executing work.

Initial implementation should use the existing backend session as the continuity anchor:

- incoming A2A requests may carry a `contextId`
- for trusted Main Sequence callers, that `contextId` may be the backend `AgentSession.uid`
- for public external callers, Astro may later issue an opaque `contextId` that maps internally to
  `AgentSession.uid`
- in both cases, the execution path must resolve or create the correct backend `AgentSession`
  through backend-authorized APIs before touching Pi/runtime/checkpoints

This keeps A2A standard at the wire boundary without removing the backend objects that make our
runtime safe.

## Public Agent Card

Status: Deferred from the first implementation pass.

Astro should eventually publish a real A2A Agent Card:

```http
GET /.well-known/agent-card.json
Accept: application/a2a+json
```

Example:

```json
{
  "name": "Main Sequence Astro",
  "description": "Main Sequence agent runtime for delegated agent work.",
  "supportedInterfaces": [
    {
      "url": "https://astro.example.com/api/a2a/v1",
      "protocolBinding": "HTTP+JSON",
      "protocolVersion": "1.0"
    },
    {
      "url": "https://astro.example.com/api/a2a/rpc",
      "protocolBinding": "JSONRPC",
      "protocolVersion": "1.0"
    }
  ],
  "provider": {
    "organization": "Main Sequence",
    "url": "https://mainsequence.ai"
  },
  "version": "2.1.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": true,
    "extendedAgentCard": true,
    "extensions": [
      {
        "uri": "https://mainsequence.ai/a2a/extensions/output-contract/v1",
        "description": "Optional output schema and strict JSON validation controls.",
        "required": false
      }
    ]
  },
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["text/plain", "application/json"],
  "skills": [
    {
      "id": "astro-orchestration",
      "name": "Astro Orchestration",
      "description": "Coordinates Main Sequence agent work using the active runtime.",
      "tags": ["mainsequence", "orchestration", "agent-runtime"]
    }
  ]
}
```

Rules:

- The Agent Card is the discovery source for public A2A clients.
- The card must advertise the exact base URL for each supported binding.
- Production URLs must be HTTPS.
- Private/internal runtime URLs, bearer tokens, pod names, and checkpoint paths must not appear in
  the public card.
- Do not implement this route until the request/response adapter and `AgentSession` mapping are
  stable enough to advertise publicly.

## HTTP+JSON REST Binding

The REST base URL is:

```text
/api/a2a/v1
```

The public endpoints are:

```text
POST /api/a2a/v1/message:send
POST /api/a2a/v1/message:stream
GET  /api/a2a/v1/tasks/{id}
GET  /api/a2a/v1/tasks
POST /api/a2a/v1/tasks/{id}:cancel
POST /api/a2a/v1/tasks/{id}:subscribe
POST /api/a2a/v1/tasks/{id}/pushNotificationConfigs
GET  /api/a2a/v1/tasks/{id}/pushNotificationConfigs/{configId}
GET  /api/a2a/v1/tasks/{id}/pushNotificationConfigs
DELETE /api/a2a/v1/tasks/{id}/pushNotificationConfigs/{configId}
GET  /api/a2a/v1/extendedAgentCard
```

### Send Message Request

```http
POST /api/a2a/v1/message:send
Content-Type: application/a2a+json
Accept: application/a2a+json
A2A-Version: 1.0
Authorization: Bearer <token>
```

```json
{
  "message": {
    "messageId": "msg-8f6c3b38-8c13-4c7b-9b7a-98370c1889db",
    "role": "ROLE_USER",
    "contextId": "agent-session-uid",
    "parts": [
      {
        "text": "What can this Astro agent do?"
      }
    ]
  },
  "configuration": {
    "acceptedOutputModes": ["application/json"],
    "historyLength": 0,
    "returnImmediately": false
  },
  "metadata": {
    "https://mainsequence.ai/a2a/extensions/output-contract/v1": {
      "strict": true,
      "schema": {
        "type": "object",
        "additionalProperties": true
      }
    }
  }
}
```

Rules:

- `message` is required.
- `message.messageId` is client-provided and idempotency-relevant.
- `message.role` must use A2A role enum values such as `ROLE_USER`.
- `message.parts` must use A2A part objects such as `{ "text": "..." }` or
  `{ "data": {...}, "mediaType": "application/json" }`.
- `configuration.acceptedOutputModes` replaces top-level `response_format` for normal media-type
  negotiation.
- Strict output schemas, if needed, must live in a declared extension under `metadata`, not as a
  random top-level field.
- Public A2A does not need `omit_reasoning`; internal reasoning and tool traces are never valid A2A
  response objects.
- `message:send` returns a `SendMessageResponse` object containing exactly one successful result
  branch: `message` or `task`.

### Direct Message Response

For simple synchronous interactions, Astro may return a direct `Message`.

Direct `Message` responses are for communication, clarification, status, or interaction that does
not need task tracking. They are not the primary place for durable task outputs.

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
```

```json
{
  "message": {
    "messageId": "msg-agent-33841969-c7a4-48be-94c1-5a18d2bbef84",
    "role": "ROLE_AGENT",
    "contextId": "ctx-a6a1cb44-3df5-4bc3-9d82-49260dd25752",
    "parts": [
      {
        "data": {
          "summary": "Astro can coordinate Main Sequence agent work.",
          "capabilities": [
            "answer session-scoped agent questions",
            "coordinate runtime-owned tasks",
            "return structured artifacts"
          ]
        },
        "mediaType": "application/json"
      }
    ]
  }
}
```

### Task Response

For work that should be tracked, Astro should return a `Task`.

Task outputs should normally be returned in `artifacts`. `status.message` is useful for
communication about the task state, but generated task results belong in `artifacts`.

```json
{
  "task": {
    "id": "task-7fceebf1-3e2f-4478-8a0c-55e7c917f76c",
    "contextId": "ctx-a6a1cb44-3df5-4bc3-9d82-49260dd25752",
    "status": {
      "state": "TASK_STATE_COMPLETED",
      "timestamp": "2026-06-17T10:30:00.000Z",
      "message": {
        "messageId": "msg-agent-33841969-c7a4-48be-94c1-5a18d2bbef84",
        "role": "ROLE_AGENT",
        "parts": [
          {
            "text": "The task completed successfully."
          }
        ]
      }
    },
    "artifacts": [
      {
        "artifactId": "artifact-27f873f0-711d-4a3b-b3c4-a9c5b5d92efa",
        "name": "result.json",
        "parts": [
          {
            "data": {
              "ok": true,
              "answer": "Structured result"
            },
            "mediaType": "application/json"
          }
        ]
      }
    ]
  }
}
```

### Long-Running Task Response

If the work will continue asynchronously, `message:send` should return a submitted or working task
immediately. The client then polls `GET /tasks/{id}`, subscribes, streams, or relies on push
notifications.

```json
{
  "task": {
    "id": "task-7fceebf1-3e2f-4478-8a0c-55e7c917f76c",
    "contextId": "ctx-a6a1cb44-3df5-4bc3-9d82-49260dd25752",
    "status": {
      "state": "TASK_STATE_SUBMITTED",
      "timestamp": "2026-06-17T12:00:00.000Z"
    }
  }
}
```

Supported task states must include the A2A task state enum values:

- `TASK_STATE_SUBMITTED`
- `TASK_STATE_WORKING`
- `TASK_STATE_INPUT_REQUIRED`
- `TASK_STATE_COMPLETED`
- `TASK_STATE_FAILED`
- `TASK_STATE_CANCELED`
- `TASK_STATE_REJECTED`
- `TASK_STATE_AUTH_REQUIRED`

### Streaming Response

`POST /api/a2a/v1/message:stream` returns `text/event-stream`.

Each SSE `data` frame must contain one A2A `StreamResponse` object:

```text
data: {"task":{"id":"task-...","contextId":"ctx-...","status":{"state":"TASK_STATE_WORKING"}}}

data: {"statusUpdate":{"taskId":"task-...","contextId":"ctx-...","status":{"state":"TASK_STATE_WORKING"}}}

data: {"artifactUpdate":{"taskId":"task-...","contextId":"ctx-...","artifact":{"artifactId":"artifact-...","parts":[{"text":"partial output"}]},"append":true,"lastChunk":false}}

data: {"statusUpdate":{"taskId":"task-...","contextId":"ctx-...","status":{"state":"TASK_STATE_COMPLETED"},"final":true}}
```

Rules:

- Do not emit Astro-specific `start`, `reasoning-start`, `tool-result`, `text-delta`, or `finish`
  chunks on the public A2A stream.
- Do not expose internal Pi tool calls, tool results, reasoning, checkpoint leases, runtime runner
  states, or provider credential details.
- Task stream events must be ordered.
- Disconnecting one stream must not necessarily cancel the task; cancellation is represented by
  `POST /tasks/{id}:cancel`.

### Task Polling

```http
GET /api/a2a/v1/tasks/task-7fceebf1-3e2f-4478-8a0c-55e7c917f76c?historyLength=10
Accept: application/a2a+json
A2A-Version: 1.0
```

Response:

```json
{
  "task": {
    "id": "task-7fceebf1-3e2f-4478-8a0c-55e7c917f76c",
    "contextId": "ctx-a6a1cb44-3df5-4bc3-9d82-49260dd25752",
    "status": {
      "state": "TASK_STATE_WORKING",
      "timestamp": "2026-06-17T10:31:00.000Z"
    }
  }
}
```

## JSON-RPC Binding

The JSON-RPC endpoint is:

```http
POST /api/a2a/rpc
Content-Type: application/json
Accept: application/json
A2A-Version: 1.0
Authorization: Bearer <token>
```

### SendMessage

`SendMessage` is the JSON-RPC binding for REST `message:send`. Its `result` must contain the same
logical `SendMessageResponse` object: exactly one of `message` or `task`.

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "msg-8f6c3b38-8c13-4c7b-9b7a-98370c1889db",
      "role": "ROLE_USER",
      "contextId": "agent-session-uid",
      "parts": [
        {
          "text": "Hello"
        }
      ]
    },
    "configuration": {
      "acceptedOutputModes": ["text/plain"],
      "returnImmediately": false
    }
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "message": {
      "messageId": "msg-agent-33841969-c7a4-48be-94c1-5a18d2bbef84",
      "role": "ROLE_AGENT",
      "contextId": "ctx-a6a1cb44-3df5-4bc3-9d82-49260dd25752",
      "parts": [
        {
          "text": "Hello from Astro."
        }
      ]
    }
  }
}
```

Task response:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "task": {
      "id": "task-7fceebf1-3e2f-4478-8a0c-55e7c917f76c",
      "contextId": "ctx-a6a1cb44-3df5-4bc3-9d82-49260dd25752",
      "status": {
        "state": "TASK_STATE_COMPLETED",
        "timestamp": "2026-06-17T12:00:00.000Z"
      },
      "artifacts": [
        {
          "artifactId": "artifact-27f873f0-711d-4a3b-b3c4-a9c5b5d92efa",
          "parts": [
            {
              "text": "2 + 2 = 4."
            }
          ]
        }
      ]
    }
  }
}
```

### SendStreamingMessage

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "SendStreamingMessage",
  "params": {
    "message": {
      "messageId": "msg-0e262e72-79fd-4792-8d06-9479d60ea53d",
      "role": "ROLE_USER",
      "contextId": "agent-session-uid",
      "parts": [
        {
          "text": "Work on this and stream progress."
        }
      ]
    }
  }
}
```

Response transport:

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream
```

Frames:

```text
data: {"jsonrpc":"2.0","id":2,"result":{"task":{"id":"task-...","contextId":"ctx-...","status":{"state":"TASK_STATE_WORKING"}}}}

data: {"jsonrpc":"2.0","id":2,"result":{"statusUpdate":{"taskId":"task-...","contextId":"ctx-...","status":{"state":"TASK_STATE_COMPLETED"},"final":true}}}
```

### GetTask, ListTasks, CancelTask, SubscribeToTask

JSON-RPC methods must use PascalCase names:

- `GetTask`
- `ListTasks`
- `CancelTask`
- `SubscribeToTask`
- `CreateTaskPushNotificationConfig`
- `GetTaskPushNotificationConfig`
- `ListTaskPushNotificationConfigs`
- `DeleteTaskPushNotificationConfig`
- `GetExtendedAgentCard`

JSON-RPC errors must use the JSON-RPC 2.0 error object. Validation errors should use
`-32602 Invalid params` with structured `google.rpc.BadRequest` details when possible.

## Internal Mapping

Astro and Main Sequence still need backend sessions and live runtimes, but these are not public A2A
wire fields.

Proposed internal mapping:

- A public A2A `contextId` maps to a Main Sequence conversation/session binding. In the Phase 1
  implementation, `message.contextId` is exactly the backend `AgentSession.uid`.
- A public A2A `task.id` maps to one backend task/turn execution record under an `AgentSession`.
- A `Message` from the client becomes a user turn for the selected Astro agent runtime.
- A direct `Message` response is allowed only when the work is simple and does not need task
  tracking.
- A `Task` response is preferred for work that touches tools, files, projects, long-running
  execution, human input, or asynchronous completion.
- Existing `AgentSession.uid` can be stored in internal task/session metadata, but should not be
  required as a public request path segment.
- Existing runtime bootstrap and warm-runner state can remain as internal optimizations behind the
  A2A adapter.
- Authorization, runtime selection, checkpoint restore, and capability materialization continue to
  use backend `AgentSession` authority.

ADR 35 is superseded. Public A2A session continuity now lives in the standard message envelope:
`message.contextId` selects the backend `AgentSession.uid`, and `message.messageId` provides
idempotency for retries.

## Output Shape And Structured JSON

For structured JSON responses:

- prefer A2A `Part.data` with `mediaType: "application/json"`
- use `configuration.acceptedOutputModes: ["application/json"]`
- when a schema is required, put it in a declared extension under `metadata`
- never leak model/tool/reasoning trace as response parts

Example request for a dictionary:

```json
{
  "message": {
    "messageId": "msg-dictionary-request",
    "role": "ROLE_USER",
    "contextId": "agent-session-uid",
    "parts": [
      {
        "text": "Return a JSON dictionary describing your capabilities."
      }
    ]
  },
  "configuration": {
    "acceptedOutputModes": ["application/json"],
    "historyLength": 0,
    "returnImmediately": false
  },
  "metadata": {
    "https://mainsequence.ai/a2a/extensions/output-contract/v1": {
      "strict": true,
      "schema": {
        "type": "object",
        "required": ["summary", "capabilities"],
        "properties": {
          "summary": { "type": "string" },
          "capabilities": {
            "type": "array",
            "items": { "type": "string" }
          }
        },
        "additionalProperties": false
      }
    }
  }
}
```

Example response:

```json
{
  "message": {
    "messageId": "msg-dictionary-response",
    "role": "ROLE_AGENT",
    "contextId": "ctx-a6a1cb44-3df5-4bc3-9d82-49260dd25752",
    "parts": [
      {
        "data": {
          "summary": "Astro coordinates Main Sequence agent work.",
          "capabilities": [
            "session-scoped agent execution",
            "structured task responses",
            "tool-backed runtime work"
          ]
        },
        "mediaType": "application/json"
      }
    ]
  }
}
```

## Compatibility Policy

The new public A2A surface should be strict.

Do not add broad compatibility aliases to the public A2A endpoints:

- no `omit_reasoning`
- no `omitReasoning`
- no `response_format`
- no `responseFormat`
- no `runtime_session_uid`
- no `agent_session_uid`
- no `thread_id`
- no Astro stream chunk events

If we must keep old routes for local debugging or backend-internal migration, mark them as:

- internal
- not A2A-compliant
- not advertised in the Agent Card
- not part of the public SDK contract

## Error Contract

REST errors should use the A2A HTTP+JSON error model:

```json
{
  "error": {
    "code": 400,
    "status": "INVALID_ARGUMENT",
    "message": "Invalid message parts.",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.BadRequest",
        "fieldViolations": [
          {
            "field": "message.parts",
            "description": "At least one part is required."
          }
        ]
      },
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "INVALID_PARAMS",
        "domain": "a2a-protocol.org"
      }
    ]
  }
}
```

JSON-RPC errors should use:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32602,
    "message": "Invalid parameters",
    "data": [
      {
        "@type": "type.googleapis.com/google.rpc.BadRequest",
        "fieldViolations": [
          {
            "field": "message.parts",
            "description": "At least one part is required."
          }
        ]
      }
    ]
  }
}
```

## Migration Plan

### Phase 0: Contract And Mapping

- [x] Reclassify ADR 35 as superseded by standard `message:send`, not public A2A runtime attach.
- [x] Define whether Phase 1 `contextId` is exactly `AgentSession.uid` or an opaque ID backed by
      `AgentSession.uid`.
- [x] Define how an external A2A request resolves, allocates, or rejects missing backend
      `AgentSession` state.
- [ ] Define the backend task/turn record that backs public A2A `task.id`.
- [x] Define the strict boundary between public A2A metadata and internal Astro/Main Sequence
      runtime metadata.

### Phase 1: A2A Request/Response Adapter

- [x] Add public HTTP+JSON A2A base `/api/a2a/v1`.
- [x] Add `POST /api/a2a/v1/message:send`.
- [x] Add `POST /api/a2a/v1/message:stream`.
- [x] Add `GET /api/a2a/v1/tasks/{id}`.
- [x] Add `GET /api/a2a/v1/tasks`.
- [x] Add `POST /api/a2a/v1/tasks/{id}:cancel`.
- [x] Add `POST /api/a2a/v1/tasks/{id}:subscribe`.
- [x] Add push notification config endpoints.
- [x] Add `GET /api/a2a/v1/extendedAgentCard`.
- [x] Add JSON-RPC endpoint `/api/a2a/rpc`.
- [x] Map JSON-RPC `SendMessage` to the same core operation as REST `message:send`.
- [x] Map JSON-RPC `SendStreamingMessage` to the same core operation as REST `message:stream`.
- [x] Add strict request validators for A2A `Message`, `Part`, `Task`, and `configuration`.
- [x] Add strict response serializers for A2A `Message`, `Task`, `Artifact`, and stream events.
- [x] Ensure public A2A streams never emit Astro/Pi internal event types.
- [x] Move existing `omit_reasoning` and strict JSON controls behind either standard
      `configuration.acceptedOutputModes` or a declared `metadata` extension.
- [x] Add tests using official A2A request/response fixtures.
- [x] Add SDK/client examples for REST and JSON-RPC.
- [x] Add endpoint-only VS Code A2A debug configurations for capabilities and dictionary JSON
      requests against `POST /api/a2a/v1/message:send`.
- [x] Add `tools/a2a/debug-endpoint.mjs` as a reusable local script that sends one simple
      standard A2A request to `POST /api/a2a/v1/message:send`.
- [x] Completely remove the old Astro-specific A2A HTTP routes instead of keeping them as 410,
      internal, or transitional routes.
- [x] Remove public docs and debug examples that present Astro runtime routes as standard A2A.

### Phase 2: Discovery

- [ ] Add a public Agent Card at `/.well-known/agent-card.json`.
- [ ] Harden `GET /api/a2a/v1/extendedAgentCard` with authenticated tenant/user-specific metadata
      only after the public base routes and `AgentSession` mapping are stable.
- [ ] Ensure the Agent Card does not expose runtime bootstrap, checkpoint, pod, or backend-secret
      implementation details.

## Consequences

Positive:

- Astro becomes interoperable with standard A2A clients.
- Public A2A stops leaking Main Sequence implementation details.
- Request and response shape becomes predictable and toolable.
- Task polling, streaming, and push notification semantics become explicit.
- Agent discovery works through Agent Cards instead of hardcoded runtime endpoints.

Negative:

- This is a breaking public contract change for callers that still use the previous Astro-specific
  A2A route shape.
- We need an adapter layer between A2A Tasks and backend `AgentSession`/runtime execution.
- Phase 1 exposes backend `AgentSession.uid` as public `message.contextId`; an opaque public mapping
  would be a future compatibility-breaking evolution.
- We need to implement task persistence and retrieval if we want `GET /tasks/{id}` to work across
  pod restarts.
- Strict A2A validation will reject existing convenience aliases.

## Open Questions

- Should `message:send` default to direct `Message` responses for short answers, or should Astro
  always create `Task` records for durability?
- Which authentication scheme should the public Agent Card advertise first: bearer token, OAuth2,
  mTLS, or another Main Sequence-specific mechanism?
- Should JSON output schemas be a Main Sequence extension, or should we require clients to express
  schema instructions through message metadata only?
- How long should transitional internal endpoints remain available after public A2A lands?
