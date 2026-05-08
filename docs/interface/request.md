# Request Contract

`POST /api/chat` accepts a JSON body compatible with assistant-ui's `ui-message-stream` request shape.

`GET /api/chat/history` accepts one of these query parameters:

- `sessionId`
- `runtime_session_id`
- `runtimeSessionId`

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
- `projectId` (string | number; optional project identity for project-scoped executor requests)
- `cwd` (string; project working directory when the executor runtime is not already pinned)
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
- `mainsequence-project-executor` is the only project implementation runtime.
- Project-scoped executor requests may rely on a deployment-pinned project cwd or supply `cwd`
  explicitly when the runtime is not already pinned.
- `POST /api/chat` no longer uses a message-level `model` field as session authority.
- Astro derives or refreshes its local `sessionModelBinding` from the request-carried `session`
  serializer and the stored session metadata.
- The authoritative model identity is session-first:
  `session.llm_provider`, `session.llm_model`, and any cached
  `session.session_metadata.session_model_binding`.
- `GET /api/chat/get_available_models` remains a control-plane discovery endpoint. It is not part
  of the normal message hot path.
- `sessionMetadata` is stored only for non-reserved keys. Astro owns reserved metadata such as
  `workflow_key`, `created_by_user`, `project_id`, `project_cwd`, `project_repo_root`,
  and `session_model_binding`.
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

Example project-executor request:

```json
{
  "threadId": "thread-hope30",
  "newChat": true,
  "agentName": "mainsequence-project-executor",
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
