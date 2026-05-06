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
- `session` (object; preferred full backend `AgentSession` serializer on resume requests)
- `projectId` (string | number; required for `mainsequence-project-coder` on `newChat: true`)
- `cwd` (string; required for `mainsequence-project-coder` on `newChat: true`)
- `sessionMetadata` (object; optional non-reserved metadata only)

## Notes

- The server reads **only the last entry** in `messages`.
- The last entry must be a `user` role message.
- If the latest user message contains the word `MOCK`, the server returns a synthetic response
  immediately for frontend testing and skips agent/session setup.
- `newChat` is treated as a UI hint for a new conversation.
- An explicit `runtime_session_id` always resumes that existing session, even if the request still
  arrives with `newChat: true`.
- Resume requests should include the full backend `AgentSession` serializer in `session`. Astro now
  treats that request-carried session object as the authority for model/provider binding and local
  metadata refresh.
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
- `POST /api/chat` no longer uses a message-level `model` field as session authority.
- Astro derives or refreshes its local `sessionModelBinding` from the request-carried `session`
  serializer and the stored session metadata.
- The authoritative model identity is session-first:
  `session.llm_provider`, `session.llm_model`, and any cached
  `session.session_metadata.session_model_binding`.
- `GET /api/chat/get_available_models` remains a control-plane discovery endpoint. It is not part
  of the normal message hot path.
- `sessionMetadata` is stored only for non-reserved keys. Astro owns reserved metadata such as
  `workflow_key`, `created_by_user`, `project_id`, `project_cwd`, `pending_runtime_bootstrap`,
  `session_model_binding`, and handoff fields.
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

Example resume request with session authority:

```json
{
  "threadId": "thread-001",
  "agentName": "astro-orchestrator",
  "userId": "user_123",
  "runtime_session_id": "456",
  "session": {
    "id": 456,
    "thread_id": "thread-001",
    "llm_provider": "openai-codex",
    "llm_model": "gpt-5.3-codex-spark",
    "runtime_config_snapshot": {
      "reasoning_effort": "on"
    },
    "session_metadata": {
      "workflow_key": "astro-orchestrator",
      "session_model_binding": {
        "source": "pi-model-registry",
        "provider": "openai-codex",
        "label": "gpt-5.3-codex-spark",
        "model": "gpt-5.3-codex-spark",
        "runConfig": {
          "reasoning_effort": "on"
        },
        "capabilities": {
          "reasoning_effort": {
            "supported": true,
            "mode": "toggle",
            "values": ["on"],
            "default": "on"
          }
        },
        "updatedAt": "2026-05-06T12:00:00.000Z",
        "piThinkingLevel": "medium"
      }
    },
    "agent": {
      "id": 123,
      "name": "astro-orchestrator"
    }
  },
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "Continue." }
      ]
    }
  ],
  "tools": {},
  "context": {
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
