# Session Storage

When backend agent registration is enabled (`BUILD_AGENTS_IN_BACKEND=1`), sessions are keyed by the backend Agent unique id
and a runtime session suffix. A new backend AgentSession is created only when `newChat: true`.

```
ASTRO_STREAM_SESSION_DIR/<agent_unique_id>__session_<n>.jsonl
ASTRO_STREAM_SESSION_DIR/<agent_unique_id>__session_<n>.meta.json
```

When registration is disabled, the fallback key is `threadId`:

```
ASTRO_STREAM_SESSION_DIR/<threadId>.jsonl
ASTRO_STREAM_SESSION_DIR/<threadId>.meta.json
```

## Session creation logic

- `newChat: true` triggers backend `start_new_session` and creates a new local session key.
- `newChat: false` requires `runtime_session_id` and reuses that existing local session.
 - New sessions include `created_by_user` set to the request `userId`.

When a new session is created, the stream emits a `new_session` chunk before `start` so the client
can capture `agent_session_id`, `session_key`, and `agent_unique_id`.
