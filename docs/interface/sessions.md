# Session Storage

When backend-backed sessions are enabled (`BUILD_AGENTS_IN_BACKEND=1`), the frontend-visible
`runtime_session_uid` is the backend `AgentSession.uid` string.

Astro does not create that session id. It only attaches to an existing backend-owned session.

```
ASTRO_STREAM_SESSION_DIR/<agent_session_uid>.jsonl
ASTRO_STREAM_SESSION_DIR/<agent_session_uid>.meta.json
```

When registration is disabled, the older local `threadId` fallback may still exist in code, but it
is not the intended production contract for chat or A2A.

## Session attachment logic

- Real `POST /api/chat` requests must include `runtime_session_uid` (or the accepted camel-case alias
  `runtimeSessionUid`). Public A2A requests derive the runtime session UID from standard
  `message.contextId`.
- Astro attaches to that existing backend session and reuses its `AgentSession.uid` as the runtime
  session key.
- Astro must not create a new backend session on behalf of the caller.
- The optional request-carried backend `session` serializer is a preferred optimization for local
  metadata refresh, not a correctness requirement.
- If request-carried `session` JSON is absent or insufficient, Astro must fetch the backend
  `AgentSession` and derive runtime state from backend authority before Pi launch.
- If local emptyDir files are missing, Astro restores backend checkpoint state before launching Pi.
- If `runtime_session_uid` is provided for `astro-orchestrator` and local wrapper files are
  missing, Astro first hydrates local metadata/checkpoint state from the existing backend session
  and then continues the same request.
- When backend-backed sessions are enabled, `threadId` is optional UI correlation only. It does not
  route resume behavior and it is not a unique session key.
- `newChat` is deprecated as a routing mechanism. Older clients may still send it as a UI hint, but
  Astro must not interpret it as permission to allocate a new backend `AgentSession`.
- `project-executor` sessions persist selected project context such as `projectId` and
  project `cwd` in local runtime metadata so later requests against the same `runtime_session_uid`
  can keep using the same project context.
- Read endpoints that require session metadata or Pi JSONL hydrate from backend checkpoint state
  before returning `session_not_found`.

## Stream events

Because Astro is no longer allowed to create sessions on the hot path, clients must not depend on a
`new_session` SSE chunk to learn session identity. The caller should already know
`runtime_session_uid` from the backend control-plane step that created the session before Astro was
called.
