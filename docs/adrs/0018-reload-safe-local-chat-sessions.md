# ADR 0018: Reload-Safe Local Chat Sessions

Status: Accepted — implemented

Date: 2026-09-27

Amends:

- [ADR 0005: Authenticated local development mode](./0005-authenticated-local-development-mode.md)
  by recording local `/api/chat` turns as chat sessions and letting a local turn outlive the
  request that started it; and
- [ADR 0017: Local direct A2A conversation discovery](./0017-local-direct-a2a-conversation-discovery.md)
  by adding a second local projection, for chat sessions, under the same owner scope.

## Context

Command Center AI is adding a local Agent source (its ADR 099) so that an application under local
development shows the same chat as in production: streaming, markdown, reasoning, tool calls, the
queue, Stop, and the model picker. It talks to local Tau through the `POST /api/chat`
`ui-message-stream` that deployed Agents serve. A2A `message:stream` forwards only text deltas, so
reasoning and tool calls would be lost.

Local `/api/chat` already served the live turn: the stream, the effective session in
`X-Agent-Session-Uid`, cancellation, and the session model. A reload lost everything else. The
turns were not recorded anywhere a client could discover, and no route read a chat session back.
ADR 0017 covers direct A2A `message:send` conversations only, as text.

The platform serves a managed AgentSession's chat history from
`GET /api/v1/agent-sessions/{uid}/history/`. It projects the session's Tau entries: text,
reasoning, and tool calls with their results. Command Center already reads that shape.

Two runtime facts shape the design:

- Tau keeps the entries of a running turn in memory until the turn commits, so the durable
  transcript cannot show a turn in progress.
- `/api/chat` cancelled its turn when the client disconnected. A reload or a closed tab during a run
  therefore cancelled the run, although the runtime contract says a caller disconnect never
  cancels an A2A Task.

## Decision

### 1. Record chat sessions, not a second transcript

Before a local `/api/chat` turn starts, the SDK records its canonical session in
`local_chat_sessions` (local schema version 6). The record holds the owner scope from ADR 0017, a
title taken from the session's first prompt (at most 80 characters), creation and last-activity
times, and a cached list summary.

The transcript is not copied. It is the Tau entries the runtime already commits atomically with
each turn: user text, assistant text, reasoning, tool calls with their arguments, and tool results
with `isError`. ADR 0017 needed a separate projection because an A2A Message has a public identity
that Tau entries do not carry. Chat history has no such identity. It is by definition the
platform's projection of Tau entries, and a second copy could only drift from it. The live stream
already shows the same reasoning and tool activity to the same local user.

Every registration, listing, and history read includes the owner scope. A session that another
authenticated user recorded, as a chat session or as a direct A2A conversation, returns a generic
not-found result. A direct A2A Message cannot continue another user's chat session either.

The cached summary (message count and latest preview) is recomputed from the entries whenever the
session's entry sequence has moved since the last listing.

### 2. Add local read routes

```text
GET /api/local/v1/chat-sessions?limit=<1..100>&cursor=<opaque>
GET /api/local/v1/chat-sessions/{sessionUid}/history
GET /api/local/v1/agent
```

The list returns `{sessions: [{sessionUid, title, messageCount, latestMessagePreview, createdAt,
updatedAt, working}], nextCursor}`, newest activity first, with keyset cursor pagination.
`sessionUid` is the canonical session that `POST /api/chat` returns in `X-Agent-Session-Uid`.

The history returns the platform's projected-history envelope, so one reader serves a managed
AgentSession and a local chat session:

```json
{
  "version": 1,
  "session": {"sessionId": "...", "threadId": "...", "agentName": "...", "agentUid": "...",
              "agentSessionUid": "...", "status": "running|completed|error",
              "startedAt": "...", "updatedAt": "...", "error": null},
  "messages": [{"id": "u_1", "role": "user", "createdAt": "...", "completedAt": null,
                "content": [{"type": "text", "text": "..."}], "provenance": {"...": "..."}}],
  "inProgressMessage": null
}
```

The projection follows the platform's rules exactly:

- only the active branch is projected;
- each user or assistant message entry with visible content becomes one message, identified `u_N`
  or `a_N`;
- `thinking` becomes `reasoning`, and a `toolCall` becomes a `tool-call` part that the matching
  `toolResult` completes with `result` (`content`, `details`) and `isError`;
- the turn's provenance stamp is attached to its user message, with `targetAgentUid`; and
- compaction and branch summaries become summary assistant messages.

There are three deliberate differences. Local mode has no Agent registry, so an agent caller's
name is never filled in. `status` describes the session's latest turn: `running`, then `error` when
that turn ended with a provider error (with its message in `error`), and otherwise `completed`.
And `inProgressMessage` is filled, as described below. Like the platform, the history returns the
whole active branch in one response.

`GET /api/local/v1/agent` returns `{name, displayName, description}` from the workspace's
`.agents/agent_card.json`, the file the platform takes a deployed Agent's name and description
from. The platform's Agent name is its display name, so `displayName` equals `name`. Without a
readable card all three are `null`; the SDK does not invent an identity.

All three routes return 409 outside local mode, like the other `/api/local/v1` routes. They return
no credentials: provider credentials are never part of Tau entries or local state.

### 3. Show the running turn

While it runs a turn, the runtime follows the turn's events: the prompt, every message Tau
completes, and the assistant message still streaming. History appends that view to the durable
branch until the turn commits. The running turn's newest assistant message, whether it is streaming
or waiting for a tool, is returned as `inProgressMessage` with `completedAt: null` and
`status: "running"`.

The live view is read before the durable entries. If the turn committed in between, the durable
entries already hold everything the live view had, and the live view is discarded.

A listed session is `working` while this process runs, starts, or settles one of its turns. It is
also `working` when another process holds the session's lease with a working turn. A process that
stopped mid-turn leaves no live lease, so its session is not reported as working forever.

### 4. Disconnect semantics for `/api/chat`

In local mode, a turn started by `POST /api/chat` runs in a task the runtime manager owns. The
request streams its events while it stays connected. A client disconnect only detaches the stream;
the turn runs to its durable end, and the list and history show its progress and result. Only
`POST /api/chat/session/cancel` stops it. A session runs one turn at a time: `POST /api/chat` for a
session with a running turn returns 409 `session_busy`, and the client queues its message.

A stopped local runtime is replaced before the session's next turn. A managed lease renewal
replaces a cancelled runtime; the local store clears the cancellation when the stopped turn
commits, so the SDK replaces the runtime itself. Before this change, every later turn in a stopped
local session failed until the idle runtime was evicted.

Managed mode is unchanged. The turn stays bound to the request that streams it, and a disconnect
cancels it. The platform's history has no in-progress message, so a detached managed turn would run
invisibly and keep the session busy.

### 5. Expose the chat stream headers to trusted origins

When `MAINSEQUENCE_TAU_TRUSTED_ORIGINS` is set, CORS now exposes `X-Agent-Session-Uid` and
`x-vercel-ai-ui-message-stream`, so a trusted cross-origin page can read the effective session. A
same-origin development proxy, as Tau Board uses under ADR 0008, remains the recommended path: the
local runtime has no inbound authentication.

## Consequences

- A local chat UI can list sessions, hydrate one with text, reasoning, and tool calls, and continue
  it after a UI reload or a Tau restart. A reload during a run shows the run in progress instead of
  cancelling it.
- One history reader serves managed and local sessions.
- Chat sessions recorded before schema version 6 are not listed. A session that takes a new
  `/api/chat` turn is recorded, and its history then includes every earlier turn of the session.
- A session that also takes A2A turns shows them in its chat history with their A2A provenance,
  as the platform history does.
- Closing a local chat no longer stops its model and tool work. Clients must call
  `POST /api/chat/session/cancel` to stop a run.
- `message:stream` still cancels its Task when the client disconnects. This decision does not
  change A2A streaming.

## Verification gate

Acceptance requires tests proving:

- a new session appears in the list after its first turn, and its history returns text, reasoning,
  and tool calls with results and `isError`, in order;
- continuing a listed session appends to the same history;
- list pagination and cursor rejection;
- the list and history survive a Tau restart, and a session continues afterwards;
- a reload during a running turn returns it in `inProgressMessage` with `status: "running"`, and a
  second turn is refused while it runs;
- a client disconnect leaves the turn running to completion;
- Stop ends a turn, and the session's next turn succeeds;
- owner-scope isolation, the workspace Agent identity, the 409 outside local mode, and the exposed
  CORS headers; and
- the routes are in the frozen HTTP operation surface.
