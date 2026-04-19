# Session Storage

When backend agent registration is enabled (`BUILD_AGENTS_IN_BACKEND=1`), the frontend-visible
`runtime_session_id` is the backend `AgentSession.id` string. A new backend AgentSession is created
only when `newChat: true`.

```
ASTRO_STREAM_SESSION_DIR/<agent_session_id>.jsonl
ASTRO_STREAM_SESSION_DIR/<agent_session_id>.meta.json
ASTRO_STREAM_SESSION_DIR/<agent_session_id>.conversation.jsonl
ASTRO_STREAM_SESSION_DIR/<agent_session_id>.history.json
```

When registration is disabled, the fallback key is `threadId`:

```
ASTRO_STREAM_SESSION_DIR/<threadId>.jsonl
ASTRO_STREAM_SESSION_DIR/<threadId>.meta.json
ASTRO_STREAM_SESSION_DIR/<threadId>.conversation.jsonl
ASTRO_STREAM_SESSION_DIR/<threadId>.history.json
```

## Session creation logic

- `newChat: true` triggers backend `start_new_session` and uses the returned backend `AgentSession.id`
  as the runtime session key.
- `newChat: false` requires `runtime_session_id` and reuses that existing local session.
- If `runtime_session_id` is provided for `astro-orchestrator` and the local session wrapper files
  are missing, Astro first tries to attach to the existing backend `AgentSession` with that id,
  hydrate the local metadata/history wrapper state, and then continue the same request without
  creating a second backend session.
- An explicit `runtime_session_id` takes precedence over `newChat: true`, so reopening an existing
  session does not create a second backend AgentSession.
- When backend registration is enabled, `threadId` is stored for metadata and UI bookkeeping only;
  it does not route resume behavior.
- New sessions include `created_by_user` set to the request `userId`.
- `mainsequence-project-coder` sessions also persist the selected `projectId` and checked-out
  project `cwd` in local session metadata so resume requests can keep using the same project context.
- `mainsequence-project-coder` sessions also freeze the git repo root in local session metadata so
  repo diff snapshots can be fetched later without the frontend sending a path.
- deterministic session-tool discovery uses the stored runtime session metadata and returns
  session-scoped relative URLs such as `repo_diff`
- `mainsequence-project-coder` sessions also persist the deterministic project-runtime bootstrap
  snapshot (SDK status, `.venv` paths, and active `mainsequence` version) so resumed turns keep
  using the same activated project environment.
- The stream wrapper appends the incoming user turn and every outgoing stream chunk to
  `.conversation.jsonl` before sending the chunk to the client
- The same write path also updates `.history.json`, which is what `GET /api/chat/history` returns

When a new session is created, the stream emits a `new_session` chunk before `start` so the client
can capture `agent_session_id`, `session_key`, and `agent_unique_id`.

The attach/hydrate path does not emit `new_session` because the backend session already existed.
