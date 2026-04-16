# History Hydration

Status: implemented.

This document defines the compact history API used to load an existing conversation without
replaying the live SSE stream.

## Goal

Return the full session history in a compact JSON payload that matches the final chat state the UI
would have after consuming the stream.

The history endpoint optimizes for:

- fast initial page load
- minimal client reconstruction work
- stable response shape for completed and in-progress sessions

It does not replay `text-delta`, `reasoning-delta`, or tool-call delta events.

## Endpoint

Implemented endpoint:

```http
GET /api/chat/history?sessionId=<runtime_session_id>
```

Accepted query parameters:

- `sessionId`
- `runtime_session_id`
- `runtimeSessionId`

## Response shape

The endpoint returns JSON, not SSE.

```json
{
  "version": 1,
  "session": {
    "sessionId": "456",
    "threadId": "thread-001",
    "agentName": "astro-orchestrator",
    "agentId": 123,
    "agentSessionId": 456,
    "status": "completed",
    "startedAt": "2026-04-11T10:00:00.000Z",
    "updatedAt": "2026-04-11T10:00:12.000Z",
    "error": null
  },
  "messages": [
    {
      "id": "u_1",
      "role": "user",
      "createdAt": "2026-04-11T10:00:00.000Z",
      "content": [
        { "type": "text", "text": "Summarize the current project state." }
      ]
    },
    {
      "id": "a_1",
      "role": "assistant",
      "createdAt": "2026-04-11T10:00:01.000Z",
      "completedAt": "2026-04-11T10:00:12.000Z",
      "content": [
        { "type": "text", "text": "Here is the current state..." }
      ]
    }
  ],
  "inProgressMessage": null
}
```

## Message model

The history payload represents assembled conversation state, not the stream transport.

Current rules:

- `messages` contains completed user and assistant messages in order
- `inProgressMessage` contains the current partial assistant message when a run is still active
- reasoning text is excluded from history hydration
- tool activity is excluded from the hydrated message list

## Storage model

Storage is split by purpose:

- `<session>.jsonl`: raw Pi session log for runtime continuity and debugging
- `<session>.meta.json`: session metadata already used by the stream runtime
- `<session>.conversation.jsonl`: append-only normalized conversation events
- `<session>.history.json`: compact hydrated transcript for fast UI load

`<session>.history.json` is read directly by the history endpoint. The endpoint does not reconstruct
history on every request from `<session>.jsonl`.

## Write strategy

Current update flow for each live chat turn:

1. On session start, initialize `<session>.history.json` if it does not exist.
2. When the request arrives, append the incoming user message once.
3. Before each outgoing SSE chunk is written, append a normalized event to
   `<session>.conversation.jsonl`.
4. Apply that event through the same reducer and rewrite `<session>.history.json` synchronously.
5. Only after the write succeeds, send the chunk to the client.

Writes are atomic:

- write to a temporary file
- rename into place

This avoids corrupting the history snapshot if the process exits mid-write.

## Running sessions

Current behavior for a still-running session:

- completed `messages` are returned immediately
- `inProgressMessage` contains any partial assistant text accumulated so far
- `session.status` remains `running` until a `finish` or `error` chunk is recorded

## Errors

Current responses:

- `400` when the session id is missing
- `404` with `session_not_found` when the runtime session does not exist
- `404` with `history_not_available` when the session exists but no compact history snapshot is present
- `200` with `session.status: "error"` when the last run failed and history was persisted

## Migration

Older sessions may not have `<session>.history.json`.

Current handling:

1. Return `404` with `history_not_available` when the session exists but no compact history snapshot is present.
2. Optional offline backfill can still be added later.

## Non-goals

- replaying the old SSE transport to rebuild the UI
- exposing raw reasoning traces in normal history hydration
- forcing the client to merge deltas into final messages
- reading the full raw session log on every history request
