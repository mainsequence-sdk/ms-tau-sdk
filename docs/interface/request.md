# Request Contract

`POST /api/chat` accepts a JSON body compatible with assistant-ui's `ui-message-stream` request shape.

`GET /api/chat/session-model` also accepts the same query parameters and returns the model binding
currently stored for that runtime session.

## Required fields

- `agentType` (string)
- `userId` (string | number)
- `runtime_session_id` (string; accepted aliases: `runtimeSessionId`, `sessionId`) for every real
  non-mock execution request
- `messages` (array) — the last entry must be the current user message

## Optional fields

- `system` (string)
- `threadId` (string)
- `newChat` (boolean; deprecated UI-only hint, ignored for session allocation)
- `context` (object)
- `tools` (object)
- `session` (object; preferred full backend `AgentSession` serializer for metadata/model refresh;
  expected on outbound A2A requests)
- `projectId` (string | number; optional project identity for project-scoped executor requests)
- `cwd` (string; project working directory when the executor runtime is not already pinned)
- `sessionMetadata` (object; optional non-reserved metadata only)

## Notes

- The server reads **only the last entry** in `messages`.
- The last entry must be a `user` role message.
- If the latest user message contains the word `MOCK`, the server returns a synthetic response
  immediately for frontend testing and skips agent/session setup.
- `runtime_session_id` is mandatory for real non-mock execution. Astro must attach to that existing
  backend session and must not create a new one.
- `newChat` is deprecated as routing input. Older clients may still send it, but Astro must ignore
  it for allocation decisions.
- Resume requests should include the full backend `AgentSession` serializer in `session`. Astro now
  treats that request-carried session object as the preferred authority for model/provider binding
  and local metadata refresh.
- Outbound A2A requests should always include the full backend `AgentSession` serializer in
  `session` together with `runtime_session_id` because the sender already has the backend session
  allocation response for the target session.
- If `session` is absent or insufficient, Astro must fetch backend session authority from
  `runtime_session_id` before Pi launch instead of proceeding with no model binding.
- `threadId` is informational/client-bookkeeping only when backend registration is enabled; it does
  not control session continuity.
- `agentType` is the backend `Agent.agent_type` value
  (unknown values return `error: unknown_agent_type`).
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
  `agent_type`, `created_by_user`, `project_id`, `project_cwd`, `project_repo_root`,
  and `session_model_binding`.
## Example request

```json
{
  "threadId": "thread-001",
  "runtime_session_id": "456",
  "agentType": "astro-orchestrator",
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
  "agentType": "astro-orchestrator",
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
      "agent_type": "astro-orchestrator",
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
      "agent_type": "astro-orchestrator"
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
  "runtime_session_id": "87",
  "agentType": "mainsequence-project-executor",
  "userId": "user_123",
  "session": {
    "id": 87,
    "thread_id": "87",
    "llm_provider": "openai-codex",
    "llm_model": "gpt-5.3-codex-spark",
    "session_metadata": {
      "agent_type": "mainsequence-project-executor"
    }
  },
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

For real A2A sends, the example session object above should be treated as abbreviated. The sender
should forward the full backend session JSON serialization under `session`, not a trimmed subset.
