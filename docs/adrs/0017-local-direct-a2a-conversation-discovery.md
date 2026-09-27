# ADR 0017: Local Direct A2A Conversation Discovery

Status: Accepted — implemented

Date: 2026-09-27

Amended: 2026-09-27 by [ADR 0018](./0018-reload-safe-local-chat-sessions.md) — local `/api/chat`
sessions have their own projection under the same owner scope, and a direct Message cannot
continue another user's chat session.

Amends:

- [ADR 0005: Authenticated local development mode](./0005-authenticated-local-development-mode.md)
  by adding an SDK-owned, user-scoped transcript projection for direct local A2A Message turns; and
- [ADR 0014: Human-readable A2A Task history and execution narratives](./0014-human-readable-a2a-task-history-and-execution-narratives.md)
  by explicitly separating direct conversation history from `Task.history`.

## Context

A local UI can call `POST /api/a2a/v1/message:send`, retain the returned `contextId`, and continue
the same Tau session. Tau's internal session entries already survive process restart. That is not a
public conversation-history contract: after a page reload the UI cannot list known direct Message
contexts or hydrate their requester/responder transcript without reading SDK-private SQLite tables
or maintaining an independent browser transcript.

Neither workaround is acceptable. Tau entries include execution-only material such as system
instructions, provider activity, tool calls, diagnostics, and internal lifecycle state. They cannot
be exposed or heuristically reinterpreted as A2A Messages. Browser-only history creates a second,
lossy source of truth that disappears across browsers and cannot prove stable Message identity.

`Task.history` does not solve this gap. It is the bounded communication history of one durable A2A
Task. The default direct Message response creates no Task and must not create a hidden Task merely
to obtain discovery.

Conversation discovery is a Main Sequence local SDK extension, not an A2A v1 operation. The SDK
must not advertise it as a standard method or add non-standard fields to A2A Task or Message
objects.

## Decision

### 1. Persist a dedicated public transcript projection

Local SQLite owns two schema-versioned projections:

- `a2a_conversations`, keyed by the effective workspace-local `contextId`, with owner scope,
  deterministic display title, and creation/last-activity timestamps; and
- `a2a_conversation_messages`, with a monotonic per-conversation sequence, stable `messageId`,
  public `ROLE_USER` or `ROLE_AGENT` role, complete public Message JSON, creation time, and the
  requester Message to which a responder Message belongs.

This projection is independent from Tau session entries and A2A Task tables. It never scans or
returns Tau entries, Task events, logs, prompts, tool activity, or model reasoning.

For a direct local `message:send` turn, the SDK:

1. validates and canonicalizes the incoming A2A Message and local `contextId`;
2. durably inserts the `ROLE_USER` Message before model execution;
3. executes the normal Tau turn;
4. durably inserts the exact `ROLE_AGENT` Message before returning HTTP success; and
5. only then marks the response delivered for snapshot scheduling.

A failed turn retains the requester Message and has no fabricated responder Message. Technical
failure evidence remains in structured logs and Tau execution state.

Requester `messageId` is the idempotency key inside one conversation. Replaying identical content
after a response was committed returns the committed responder Message without another model call.
Reusing the identifier with different content returns conflict. At most one responder Message can
be linked to one requester Message.

### 2. Add explicit local extension reads

The SDK exposes:

```text
GET /api/local/v1/conversations?limit=<1..100>&cursor=<opaque>
GET /api/local/v1/conversations/{contextId}/messages?limit=<1..200>&beforeSequence=<n>
```

The list response contains `contextId`, deterministic `title`, `messageCount`, bounded latest-text
preview, `createdAt`, `updatedAt`, and an opaque `nextCursor`. Ordering is stable by descending
last activity and context identity.

The Message response contains the same conversation metadata and ordered records with `sequence`,
the complete public `message`, and `createdAt`. An omitted `beforeSequence` returns the latest
bounded tail oldest-to-newest. `nextBeforeSequence` is supplied only when older records exist.

The listed canonical `contextId` is accepted unchanged by `message:send`, so a client can hydrate
and continue the conversation after UI or Tau restart. These routes return a capability error in
managed mode; managed AgentSession discovery remains platform-owned.

### 3. Scope reads to the authenticated local process principal

Local HTTP callers do not receive or resend the Main Sequence JWT. The Tau process owns one
authenticated Main Sequence principal, established by the JWT environment contract and validated
during startup by real platform/provider operations. Conversation records store only a one-way
owner-scope digest derived from a stable JWT subject claim. A credential fingerprint is used only
as a fallback for opaque test or legacy credentials. Access and refresh tokens are never persisted.

Every discovery, history, append, and replay query includes that owner scope. A different
authenticated principal using the same OS workspace receives an empty list and a generic not-found
result for another scope's context. The SQLite path remains additionally scoped to the canonical
workspace and protected by local filesystem permissions.

This is process authentication, not browser bearer-token authentication. All clients accepted by
one local Tau process act as that process's authenticated user, as already true for live provider
and Main Sequence MCP calls. Loopback remains the safe default. An explicit non-loopback bind is a
privileged exposure and continues to emit a high-visibility warning.

### 4. Make the persistence cut explicit

Existing Tau entries do not contain the durable public responder identity and original public
projection required to reconstruct this contract reliably. The SDK does not invent Message IDs,
infer public Messages from execution entries, or expose historical internals.

Conversation discovery therefore starts with Messages accepted after schema version 5 is active.
Pre-existing Tau sessions and A2A Tasks remain intact but do not appear as direct conversations
unless a new direct Message is accepted under this contract. There is no dual read path.

## Consequences

- UI clients can discover, hydrate, and continue direct local conversations after reload or process
  restart without coupling to SQLite.
- Direct conversation history, Task history, and Tau execution history have separate ownership and
  cannot accidentally leak into each other.
- The SDK stores one additional public Message projection; this deliberate duplication provides a
  stable API rather than making runtime internals public.
- A client that needs asynchronous lifecycle, progress, interruption, cancellation, or Task
  subscription must still select `responseKind: "task"` and use the Task API.
- Existing conversations cannot be retroactively reconstructed and are intentionally absent from
  this new discovery surface.

## Verification gate

Acceptance requires tests proving:

- requester-before-execution and responder-before-success ordering;
- exact replay and conflicting Message-ID behavior;
- stable list and Message pagination;
- restart hydration and continuation with the listed `contextId`;
- owner-scope isolation without persisted credentials;
- direct Message history never reads Tau entries or Task history; and
- the routes are present in the frozen HTTP operation surface and rejected outside local mode.
