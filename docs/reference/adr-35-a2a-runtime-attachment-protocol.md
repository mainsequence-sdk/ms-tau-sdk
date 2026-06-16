# ADR 35: A2A Session Runtime Attachment Protocol

Status: Accepted
Date: 2026-06-16
Implementation Status: Partially implemented

## Context

Astro currently exposes a one-turn HTTP/SSE execution endpoint. The caller sends a message plus
runtime/session/output options, Astro resolves the existing backend session metadata, prepares local
runtime state, finds or starts a Pi runner, streams one response, and the HTTP request ends.

That one-turn shape is not a proper A2A session attachment protocol and is not part of the new A2A
client contract.

For machine-to-machine A2A clients, the backend session lifecycle is already handled before Astro is
called. Astro should not create backend `AgentSession` records in this protocol. Astro's concern is:

- attach an existing backend-owned agent session to its live Astro/Pi runtime
- start or reuse the Pi runtime for that backend session
- accept multiple turns against that attached session runtime
- cancel, detach, or expire that attached runtime cleanly

## Problem

The one-turn endpoint collapses three different concepts into one request:

- backend agent session identity
- live Astro/Pi runtime attachment
- one user turn

As a result, a Python A2A client repeatedly sends a full request envelope and Astro repeatedly has to
resolve enough context to decide whether it can reuse anything. Even when a warm runner exists
internally, the public contract is still "send one turn and hope the runtime can be reused."

That creates avoidable complexity and latency:

- the client cannot explicitly attach the existing backend session to a live runtime
- Astro has no explicit attach/detach lifecycle from the caller
- startup/preparation work is hidden inside every chat request
- repeated turns cannot cleanly target an already-attached session runtime
- cancellation and detachment are session-level side effects instead of explicit runtime operations

## Decision

Astro should add a first-class A2A session runtime attachment protocol.

The protocol starts after the caller already has an existing backend session identity. Backend
session creation or lookup is not part of this ADR.

The public workflow should be:

```text
attach/open runtime for existing backend session
send one or more turns to that attached session runtime
cancel an active turn when needed
detach when the caller is done
```

There is no second public runtime identifier. The public resource key is the existing backend
`agent_session_uid`. Astro may keep internal in-memory runtime state, but that is implementation
detail.

## REST Contract

### Attach Session Runtime

```http
POST /api/a2a/sessions/{agent_session_uid}/runtime
```

Request:

```json
{
  "user_uid": "e2a4f38a-1b5f-40a3-974f-70bc8f065b3f",
  "thread_id": "0b2701a1-e777-4cfe-8437-b94025f00069",
  "agent_type": "astro-orchestrator"
}
```

Rules:

- `{agent_session_uid}` references an existing backend-owned session UID.
- `user_uid` identifies the backend user that owns or is authorized for the existing session.
- `thread_id` is the existing conversation/thread identity when the caller has one.
- `agent_type` selects the Astro runtime identity.
- Attach must not create a backend session.
- Attach must return immediately after registering the session runtime attachment.
- Attach starts runtime bootstrap and runtime preparation asynchronously.
- Attach must not wait for Pi readiness, checkpoint preparation, capability materialization, or
  provider credential hydration.
- Attach may return the existing compatible runtime attachment for the same backend session.
- Attach binds an existing backend session to its live Astro/Pi runtime.

Response:

```json
{
  "ok": true,
  "agent_session_uid": "0b2701a1-e777-4cfe-8437-b94025f00069",
  "thread_id": "0b2701a1-e777-4cfe-8437-b94025f00069",
  "agent_type": "astro-orchestrator",
  "state": "starting",
  "expires_at": "2026-06-16T16:30:00.000Z"
}
```

The attach response should not include a generated `endpoints` map. Routes are stable documented API
paths, and returning an endpoints object adds contract noise without solving the real problem.

### Runtime Status

```http
GET /api/a2a/sessions/{agent_session_uid}/runtime
```

Response:

```json
{
  "ok": true,
  "agent_session_uid": "0b2701a1-e777-4cfe-8437-b94025f00069",
  "state": "ready",
  "runner": {
    "kind": "pi-rpc",
    "ready": true
  },
  "preflight": {
    "ready": true,
    "checkpoint": "skipped",
    "capabilities": "ready",
    "provider_credentials": "ready"
  },
  "expires_at": "2026-06-16T16:30:00.000Z"
}
```

This endpoint lets a client wait for readiness without sending a turn.

Attach readiness is intentionally separate from runtime readiness. The attach call is session
runtime registration, not a blocking startup call. Clients that need readiness before sending a turn
should poll this endpoint, or call chat and let the chat request wait according to its own
per-request policy.

### Send Turn

```http
POST /api/a2a/sessions/{agent_session_uid}/runtime/chat
```

The response transport remains SSE for streaming turns.

Request:

```json
{
  "message": "Return a JSON with only 2 keys.",
  "response_format": {
    "type": "json_object",
    "strict": true
  },
  "json_repair": {
    "attempts": 3
  },
  "omit_reasoning": true,
  "runtime_turn_timeout_seconds": 900
}
```

Rules:

- The turn request targets the attached runtime for `{agent_session_uid}`.
- The turn request should not repeat backend session identity fields.
- The turn request carries only per-turn inputs and output controls.
- Astro serializes turns for the attached session runtime.
- Standard persistent sessions preserve checkpoint/session-history safety.
- Ephemeral sessions preserve the no-checkpoint/no-persistent-history behavior selected at attach.
- Strict JSON and reasoning suppression behavior follows ADR 34.
- Runtime turn timeout remains per request. Omitted or `0` means Astro does not kill by timer.

### Cancel Active Turn

```http
POST /api/a2a/sessions/{agent_session_uid}/runtime/cancel
```

Request:

```json
{
  "reason": "client_requested"
}
```

Rules:

- Cancels the active turn for this attached session runtime.
- Does not detach the runtime unless the caller asks to detach or the runtime becomes unhealthy.
- Error reporting must distinguish explicit cancel, client disconnect, timeout, and runtime failure.

### Detach Session Runtime

```http
POST /api/a2a/sessions/{agent_session_uid}/runtime/detach
```

Request:

```json
{
  "reason": "client_done"
}
```

Rules:

- Releases the runtime attachment for the backend session.
- Astro may keep the runner alive until idle TTL expires or stop it immediately based on runtime
  policy.
- Detach must not delete or close the backend session.

## Session Runtime Semantics

The backend `agent_session_uid` is the public identity for this protocol. Astro does not mint another
public runtime identifier.

There is one live Pi runtime/process for an attached backend session, and Astro serializes turns
against that runtime. If the same backend session is already attached with compatible runtime
options, attach may return the existing attachment state.

Astro's internal runtime attachment state binds:

- backend `agent_session_uid`
- thread id
- agent type/runtime profile
- selected session mode
- live or starting Pi runner
- prepared runtime state
- expiry/idle policy

Every session-runtime endpoint must authenticate the request and verify authorization for the
underlying backend session.

Expired or missing attachments should return:

```json
{
  "ok": false,
  "error": "session_runtime_not_attached",
  "message": "No live runtime is attached for this backend session."
}
```

## Python Client Shape

The intended Python client workflow is:

```python
runtime = client.attach_session_runtime(
    agent_session_uid=session_uid,
    user_uid=user_uid,
    thread_id=thread_uid,
    agent_type="astro-orchestrator",
)

runtime.wait_until_ready()

response = runtime.chat(
    message="Return a JSON with only 2 keys.",
    response_format={"type": "json_object", "strict": True},
    omit_reasoning=True,
)

runtime.detach()
```

The client should not rebuild the full runtime/session envelope for every turn after attachment.

## Removed From The A2A Contract

`POST /api/a2a/chat` is not part of the new A2A client contract.

New A2A clients must not use a one-request wrapper that hides attach, turn execution, and detach
inside a single request. The supported client flow is:

```text
attach session runtime
send one or more turns to the attached runtime
cancel the active turn when needed
detach when done
```

If an old one-turn route still exists in code, it is legacy surface area, not this ADR's protocol.

## Non-Goals

- Do not create backend sessions in Astro.
- Do not remove agent skills or session capabilities.
- Do not remove provider credential handling.
- Do not remove checkpoint/session safety from the standard path.
- Do not require clients to consume a generated endpoints map.
- Do not introduce a second public runtime identity separate from `agent_session_uid`.

## Implementation Plan

- [x] Add a session runtime registry keyed by backend `agent_session_uid`.
- [x] Track runtime state as `starting`, `ready`, `busy`, `failed`, or `detached`.
- [x] Store only Astro runtime attachment state in the registry: session UID, thread UID, agent type,
  session mode, runner/preflight status, current turn state, expiry, and last error.
- [x] Add `POST /api/a2a/sessions/{agent_session_uid}/runtime`.
- [ ] Make attach validate authorization for the existing backend session UID without creating a new
  backend session.
- [x] Make attach return immediately after registering or reusing the session runtime attachment.
- [x] Start Pi runner bootstrap and preflight preparation asynchronously from attach.
- [x] Keep startup/preflight work out of the attach response path: no waiting for Pi readiness,
  checkpoint preparation, capability materialization, or provider credential hydration.
- [x] Add `GET /api/a2a/sessions/{agent_session_uid}/runtime`.
- [x] Return clear status fields for runtime readiness, runner readiness, preflight readiness,
  expiry, and last error.
- [x] Add `POST /api/a2a/sessions/{agent_session_uid}/runtime/chat`.
- [x] Route chat turns to the attached session runtime and serialize same-session turns.
- [x] Reuse the already-attached Pi RPC runner when it is ready.
- [x] If chat arrives while the runtime is still starting, wait according to the chat request policy
  rather than blocking attach.
- [x] Preserve standard checkpoint/session-history safety for standard sessions.
- [x] Remove `session_mode` and the no-checkpoint/no-persistent-history branch from the A2A session
  runtime contract.
- [x] Preserve ADR 34 output controls for strict JSON, JSON repair, and reasoning suppression.
- [x] Add `POST /api/a2a/sessions/{agent_session_uid}/runtime/cancel`.
- [x] Make cancel stop only the active turn for the attached session runtime.
- [ ] Report explicit cancel, client disconnect, timeout, and runtime failure as distinct outcomes.
- [x] Add `POST /api/a2a/sessions/{agent_session_uid}/runtime/detach`.
- [x] Make detach release the runtime attachment without deleting or closing the backend session.
- [ ] Decide runner cleanup from runtime policy after detach: idle TTL or immediate stop.
- [x] Define the new A2A client contract as session-runtime only.
- [x] Remove `POST /api/a2a/chat` from the A2A server surface.
- [ ] Update Python/Main Sequence client helpers to attach once, wait for readiness, send multiple
  turns, cancel when needed, and detach when done.
- [ ] Add unit tests for attach immediate-return behavior, status transitions, chat routing,
  same-session serialization, cancel, detach, and missing attachment errors.
- [ ] Add integration coverage proving second and later turns do not repeat the full one-turn
  startup path.
- [ ] Update API docs and examples to use `agent_session_uid` consistently.
