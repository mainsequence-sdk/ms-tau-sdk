# Response Contract

Responses are **SSE** with:

- `Content-Type: text/event-stream`
- `X-Stream-Protocol: ui-message-stream`
- `X-Thread-Id`
- `X-Agent-Id` (backend Agent id)
- `X-Agent-Unique-Id` (deterministic identity)
- `X-Agent-Session-Id` (backend AgentSession id when created)
- `X-Session-Key` (runtime session key)

Each SSE event uses:

```
event: message
data: { ...chunk... }
```

The stream always ends with:

```
data: [DONE]
```

`GET /api/chat/history` returns compact JSON history, not SSE. See
[`history-hydration.md`](./history-hydration.md) for the response shape.
`GET /api/chat/session-tools` also returns JSON, not SSE. It advertises deterministic backend tools
available for the runtime session, including session-scoped URLs such as `repo_diff`.
If no deterministic tools are available, or if local metadata is not available yet, it returns
`available_tools: {}` rather than an error.
`GET /api/chat/session-model` also returns JSON, not SSE. It exposes the model binding stored for
the runtime session.
`GET /api/chat/diff` also returns JSON, not SSE. It exposes the current deterministic git diff
snapshot for a `mainsequence-project-coder` session, keyed only by the runtime session id.
If the latest user message contains the word `MOCK`, `POST /api/chat` returns a synthetic SSE
response immediately and does not create or resume a backend session.

## Chunk types

The server emits standard assistant-ui stream chunks:

- `new_session` (only on `newChat: true` after backend AgentSession creation)
- `session_switch` (when the orchestrator requests a real handoff into `mainsequence-project-coder`)
- `start`
- `reasoning-start` / `reasoning-delta` / `reasoning-end`
- `text-start` / `text-delta` / `text-end`
- `tool-call-start` / `tool-call-delta` / `tool-call-end`
- `tool-result`
- `finish`
- `error`

When `mainsequence-project-coder` is starting a fresh project-local session, the runtime also uses
the normal tool-call and tool-result chunks to expose deterministic bootstrap steps such as:

- `mainsequence project sdk-status --path . --json`
- `mainsequence project build_local_venv --path .`
- `uv sync`
- virtualenv activation
- active virtualenv `mainsequence` version inspection

The server may also emit a short assistant progress message immediately before those runtime tool
events so the user sees that project-environment verification is in progress.

Every chunk also includes:

```json
{ "agent_id": 123 }
```

The `new_session` chunk payload includes:

```json
{
  "type": "new_session",
  "new_session": {
    "agent_session_id": 456,
    "session_key": "456",
    "agent_unique_id": "astro-orchestrator_user_123",
    "thread_id": "thread-001",
    "agent_id": 123
  },
  "agent_id": 123
}
```

The `session_switch` chunk payload includes:

The server may emit a short assistant handoff message immediately before `session_switch` so the
user sees which project is active and that the conversation is moving into the project coding
agent.

After that `session_switch`, the same response may continue with coder-session chunks:

- `new_session`
- a short assistant runtime status message
- deterministic bootstrap tool events
- either a ready message or continued `mainsequence-project-coder` output

```json
{
  "type": "session_switch",
  "session_switch": {
    "from_agent_name": "astro-orchestrator",
    "to_agent_name": "mainsequence-project-coder",
    "project_id": "42",
    "cwd": "/absolute/path/to/checked-out-project",
    "thread_id": "thread-001",
    "agent_id": 456,
    "agent_unique_id": "mainsequence-project-coder_user_123_42",
    "agent_session_id": 789,
    "session_key": "789",
    "runtime_session_id": "789",
    "initial_task": null,
    "summary": "Hope 30 is checked out locally and ready for project-local work."
  },
  "agent_id": 456
}
```
