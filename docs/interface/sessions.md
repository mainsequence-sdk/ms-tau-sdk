# Session Storage

When backend-backed sessions are enabled (`BUILD_AGENTS_IN_BACKEND=1`), the frontend-visible
`runtime_session_id` is the backend `AgentSession.id` string. A new backend AgentSession is created
only when `newChat: true`.

```
ASTRO_STREAM_SESSION_DIR/<agent_session_id>.jsonl
ASTRO_STREAM_SESSION_DIR/<agent_session_id>.meta.json
```

When registration is disabled, the fallback key is `threadId`:

```
ASTRO_STREAM_SESSION_DIR/<threadId>.jsonl
ASTRO_STREAM_SESSION_DIR/<threadId>.meta.json
```

Frontend chat history shape is owned by Astro. The local `<session>.conversation.jsonl` and
`<session>.history.json` files are runtime cache files; when that cache is missing, Astro rebuilds
the frontend transcript from the backend `AgentSession` record plus latest checkpoint
`bundle.pi_session_jsonl`.

## Session creation logic

- `newChat: true` triggers backend `start_new_session` and uses the returned backend `AgentSession.id`
  as the runtime session key.
- `newChat: true` also requires the backend integer `agentId` unless Astro is already attaching to
  a hydrated backend-owned session.
- `newChat: false` requires `runtime_session_id` and reuses that existing session. If local
  emptyDir files are missing, Astro restores the backend checkpoint before launching Pi.
- If `runtime_session_id` is provided for `astro-orchestrator` and the local session wrapper files
  are missing, Astro first tries to attach to the existing backend `AgentSession` with that id,
  hydrate local metadata and checkpoint state, and then continue the same request without
  creating a second backend session.
- An explicit `runtime_session_id` takes precedence over `newChat: true`, so reopening an existing
  session does not create a second backend AgentSession.
- When backend-backed sessions are enabled, `threadId` is optional metadata/UI correlation only; it is
  not unique and it does not route resume behavior.
- New sessions include `created_by_user` set to the request `userId`.
- `mainsequence-project-executor` sessions persist the selected project context such as `projectId`
  and project `cwd` in local session metadata so resume requests can keep using the same project context.
- The stream wrapper keeps a local history cache for the active process. The backend does not store
  Astro's frontend history snapshot shape.
- `GET /api/chat/history` returns Astro's frontend history shape from local `.history.json` when it
  exists; otherwise it reconstructs history from backend `AgentSession.id` plus checkpoint state.
- Read endpoints that require session metadata or Pi JSONL hydrate from backend checkpoint state
  before returning `session_not_found`.

When a new session is created, the stream emits a `new_session` chunk before `start` so the client
can capture `agent_session_id`, `session_key`, `runtime_session_id`, `agent_name`, and
`agent_unique_id` when the backend included it.

The attach/hydrate path does not emit `new_session` because the backend session already existed.
