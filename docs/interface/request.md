# Request Contract

`POST /api/chat` accepts a JSON body compatible with assistant-ui's `ui-message-stream` request shape.

`GET /api/chat/history` accepts one of these query parameters:

- `sessionId`
- `runtime_session_id`
- `runtimeSessionId`

`GET /api/chat/diff` accepts the same query parameters and returns the current deterministic git
diff snapshot for a `mainsequence-project-coder` session.

`GET /api/chat/session-tools` also accepts the same query parameters and returns the deterministic
backend tools currently advertised for that runtime session.
If local metadata is not available yet, this endpoint still returns `200` with
`available_tools: {}` so the UI can treat tool discovery as best-effort.

`GET /api/chat/session-model` also accepts the same query parameters and returns the model binding
currently stored for that runtime session.

## Required fields

- `agentName` (string)
- `userId` (string | number)
- `messages` (array) — the last entry must be the current user message

## Optional fields

- `system` (string)
- `threadId` (string)
- `newChat` (boolean)
- `context` (object)
- `tools` (object)
- `runtime_session_id` (string; required when `newChat` is `false`)
- `projectId` (string | number; required for `mainsequence-project-coder` on `newChat: true`)
- `cwd` (string; required for `mainsequence-project-coder` on `newChat: true`)
- `model` (object | null; optional lightweight session model override)

## Notes

- The server reads **only the last entry** in `messages`.
- The last entry must be a `user` role message.
- If the latest user message contains the word `MOCK`, the server returns a synthetic response
  immediately for frontend testing and skips agent/session setup.
- `newChat` is treated as a UI hint for a new conversation.
- An explicit `runtime_session_id` always resumes that existing session, even if the request still
  arrives with `newChat: true`.
- `threadId` is informational/client-bookkeeping only when backend registration is enabled; it does
  not control session continuity.
- `agentName` must match the backend registry (unknown agents return `error: unknown_agent`).
- When backend registration is enabled, `runtime_session_id` is the backend `AgentSession.id` string.
- `mainsequence-project-coder` uses a project-scoped deterministic identity, so its initial
  request must include both `projectId` and the checked-out project `cwd`.
- Resume requests for `mainsequence-project-coder` can omit `projectId` and `cwd` when the stored
  session metadata already contains them.
- `GET /api/chat/session-tools` is the canonical discovery endpoint for deterministic session-level
  backend tools such as `repo_diff`.
- `GET /api/chat/session-tools` must not fail only because no tools are available; no tools is
  represented as an empty `available_tools` object.
- `model`, when present on `POST /api/chat`, is stored in the runtime session metadata before Pi
  starts for that turn.
- `model.source` plus `model.model` must match one of the records from
  `GET /api/chat/get_available_models` under `providers[*].models[*]`.
- `model: null` clears any previously stored session model binding.
- `GET /api/chat/diff` does not require the frontend to send `projectId` or `cwd`; the server
  resolves the frozen repo root from the stored coding-session metadata.
- On a new `mainsequence-project-coder` session, the runtime prepares the checked-out project before
  Pi starts by running `sdk-status`, `build_local_venv`, `uv sync`, and then activating the
  project's `.venv` for the session process.
- Those deterministic bootstrap steps are emitted as tool-style stream events on the
  `mainsequence-project-coder` session after a short runtime status message and before normal
  Pi-driven assistant work begins.
- When the orchestrator hands off with `session_switch`, the same response may continue immediately
  with those coder-session bootstrap events instead of waiting for another user turn.

## Example request

```json
{
  "threadId": "thread-001",
  "newChat": true,
  "agentName": "astro-orchestrator",
  "userId": "user_123",
  "system": "optional system prompt",
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "Summarize this workspace." }
      ]
    }
  ],
  "tools": {},
  "context": {
    "appId": "astro-ui",
    "surfaceId": "command-center",
    "userId": "user_123"
  }
}
```

Example project-coder request:

```json
{
  "threadId": "thread-hope30",
  "newChat": true,
  "agentName": "mainsequence-project-coder",
  "userId": "user_123",
  "projectId": "42",
  "cwd": "/absolute/path/to/hope30",
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "Summarize the current project context and continue from here." }
      ]
    }
  ],
  "tools": {},
  "context": {
    "userId": "user_123"
  }
}
```
