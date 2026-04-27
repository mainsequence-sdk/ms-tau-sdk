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
Reasoning is returned as assembled structured content parts, not as replayed SSE deltas and not as
literal provider `<think>` tags.

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
        { "type": "reasoning", "text": "I should inspect the current project state first." },
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
- user messages reconstructed from Pi checkpoints strip Astro's internal prompt wrapper and return
  only the frontend-visible latest user message
- assistant reasoning is preserved as `{ "type": "reasoning", "text": "..." }`
- Pi native `thinking` blocks and literal provider `<think>...</think>` text are normalized into
  structured `reasoning` parts
- tool activity is excluded from the hydrated message list

## Storage model

Storage is split by purpose. Astro owns the frontend history projection returned by this endpoint.
The backend stores generic session/checkpoint data, not Astro's hydrated frontend snapshot shape.

- `<session>.jsonl`: raw Pi session log for runtime continuity and debugging
- `<session>.meta.json`: session metadata already used by the stream runtime
- `<session>.conversation.jsonl` and `<session>.history.json`: local runtime cache for the compact
  frontend transcript
- backend `AgentSession`: generic session identity, optional thread correlation, lifecycle status, agent fields,
  timestamps, and error detail used to reconstruct the session envelope when local cache is missing
- backend latest checkpoint: generic Pi runtime checkpoint containing `bundle.pi_session_jsonl`, used
  to rebuild the frontend message transcript when local cache is missing

`GET /api/chat/history` reads local `.history.json` first. If that file is missing but the backend
`AgentSession` exists, Astro fetches
`GET /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint/latest/`, projects
`bundle.pi_session_jsonl` into frontend user/assistant messages, writes the rebuilt
`.history.json` cache, and returns the rebuilt snapshot.

If the deployed backend does not yet expose `checkpoint/latest/`, Astro uses a compatibility
fallback: acquire a short checkpoint lease, call `checkpoint/restore/`, release the lease, then
project the returned `bundle.pi_session_jsonl`. This path is only for older backends and is slower
than the read-only endpoint.

Astro owns this projection. The backend must not store the frontend history response shape.
If the backend `AgentSession.thread_id` is blank, Astro uses `runtime_session_id` as the
frontend-facing `threadId` fallback. `thread_id` is not a uniqueness boundary.

## Write strategy

Current update flow for each live chat turn:

1. On session start, initialize the local cache if it does not exist.
2. When the request arrives, append the incoming user message once and update the local compact
   snapshot.
3. Before each outgoing SSE chunk is written, apply the chunk through the reducer and update the
   local cache.
4. At stream completion, keep the compact local snapshot current.
5. Only Pi/runtime continuity depends on the local Pi JSONL checkpoint path.

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
- `404` with `history_not_available` when neither local history nor a backend checkpoint is
  available to reconstruct the transcript
- `502` with `backend_session_history_reconstruction_failed` when Astro cannot fetch or parse the
  backend `AgentSession` or latest checkpoint needed for reconstruction
- `200` with `session.status: "error"` when the backend session or local history records a failed
  run

## Migration

Older sessions may not have local `.history.json` after pod-local storage is replaced.

Current handling:

1. Return local `.history.json` when it exists.
2. Fall back to backend `AgentSession` plus backend `checkpoint/latest/`.
3. Rebuild the message transcript from `bundle.pi_session_jsonl` and cache the rebuilt
   `.history.json` locally.
4. Return `404` with `history_not_available` when neither local cache nor backend checkpoint data
   can produce the frontend transcript.

## Non-goals

- replaying the old SSE transport to rebuild the UI
- exposing raw provider reasoning tags in normal history hydration
- forcing the client to merge deltas into final messages
- reading the full raw session log when the local `.history.json` cache exists
