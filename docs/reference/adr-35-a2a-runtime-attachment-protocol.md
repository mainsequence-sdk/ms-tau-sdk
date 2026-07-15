# ADR 35: Superseded A2A Runtime Attachment Protocol

Status: Superseded
Date: 2026-06-16
Superseded: 2026-06-19
Implementation Status: Removed from the public API

## Context

This ADR originally proposed a separate public runtime attachment/status protocol for A2A
sessions. That design split A2A usage into a runtime preparation step and a message delivery step.

After implementation and debugging, that split proved confusing and unnecessary for public A2A
clients. It also encouraged polling a non-message endpoint and made it unclear which call owned
conversation continuity.

## Decision

The public A2A contract is now single-entry message delivery:

- `POST /api/a2a/v1/message:send` for normal request/response A2A.
- `message.contextId` is the existing backend `AgentSession.uid`.
- `message.messageId` is required and identifies the logical message within that context.
- Reusing the same `(contextId, messageId)` with the same request is idempotent.
- Reusing the same `(contextId, messageId)` with different request content is a conflict.
- Runtime bootstrap, checkpoint hydration, provider credential preparation, and warm-runner reuse are
  internal implementation details behind `message:send`.

No public prewarm/status endpoint is part of the active A2A contract.

## Rationale

A2A conversation continuity belongs to the A2A message envelope, not to an Astro-specific runtime
resource. The backend already owns `AgentSession` creation and target-session allocation. Astro only
needs to execute turns against that existing session.

This keeps the client flow simple:

```text
allocate or reuse backend AgentSession
send message with contextId = AgentSession.uid
retry same logical message with the same messageId
send the next logical message with a new messageId and same contextId
```

The first message for a cold pod/session may still perform internal preparation. Subsequent messages
reuse the same warm runner while the pod/process keeps it alive.

## Consequences

- Clients do not call a separate runtime attach or runtime status endpoint.
- The only required public A2A message path is `POST /api/a2a/v1/message:send`.
- Existing internal bootstrap code may remain as reusable functions.
- Public route tables and debug configurations must not advertise the superseded runtime endpoint.
- The active client-facing contract is documented in `docs/a2a/README.md`.
