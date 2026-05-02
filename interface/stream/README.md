# Astro Stream Interface

This interface exposes Astro over HTTP using assistant-ui's `ui-message-stream` protocol over SSE.
For the canonical contract, see the docs at `docs/interface/`.

## Run

```bash
npm run pi:stream
```

Defaults:

- host: `0.0.0.0`
- port: `8787`

Environment overrides:

- `ASTRO_STREAM_HOST`
- `ASTRO_STREAM_PORT`
- `ASTRO_STREAM_TRUSTED_ORIGINS` (comma-separated browser origins allowed for cross-origin requests)
- `ASTRO_STREAM_SESSION_DIR` (default `<repo>/.astro/stream-sessions`; in containers use
  `/session-state/sessions`)
- `ASTRO_STREAM_LOG_REQUEST_BODIES` (`1` enables request payload debug logging)

`ASTRO_STREAM_CORS_ORIGIN` is supported as a deprecated single-origin fallback, but the preferred
configuration is `ASTRO_STREAM_TRUSTED_ORIGINS`.

In `docker-compose.yml`, the stream service uses container-local runtime state under
`/home/appuser/.astro-container-data` and mounts the shared tmpfs-backed `astro_session_emptydir`
volume at `/session-state`. It sets `ASTRO_STREAM_SESSION_DIR=/session-state/sessions` so local
Docker follows the emptyDir checkpoint storage model instead of durable local session storage. The
checkpoint sidecar shares the same `/session-state` mount, uses the same
`ASTRO_CHECKPOINT_HOLDER_ID`, and flushes complete checkpoint bundles to the backend. The image runs
as non-root `appuser`.

## Endpoints

### `POST /api/chat`

Send a request compatible with assistant-ui's `ui-message-stream` runtime:

```json
{
  "newChat": true,
  "agentName": "astro-orchestrator",
  "userId": "user_123",
  "system": "optional system prompt",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "the exact message the user just typed"
        }
      ]
    }
  ],
  "tools": {},
  "threadId": "optional-thread-id",
  "context": {
    "appId": "...",
    "appTitle": "...",
    "currentPath": "...",
    "surfaceId": "...",
    "surfaceTitle": "...",
    "surfaceActions": [],
    "surfaceContextSource": "surface",
    "surfaceDetails": {},
    "surfaceSummary": "...",
    "userId": "..."
  }
}
```

To start a project-scoped coding session directly, use:

```json
{
  "newChat": true,
  "agentName": "mainsequence-project-coder",
  "userId": "user_123",
  "projectId": "42",
  "cwd": "/absolute/path/to/checked-out-project",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Read the current project context and get ready to continue here."
        }
      ]
    }
  ],
  "tools": {},
  "context": {
    "userId": "user_123"
  }
}
```

The response is always SSE and uses `event: message` with one JSON chunk per event.

To bind or override the session model on the same request, include:

```json
{
  "model": {
    "source": "pi-model-registry",
    "provider": "openai",
    "model": "gpt-5.4",
    "runConfig": {
      "reasoning_effort": "high"
    }
  }
}
```

The response includes `X-Thread-Id`. When backend registration succeeds, it also includes:

- `X-Agent-Id`
- `X-Agent-Unique-Id`
- `X-Agent-Session-Id` (when a new session is created)
- `X-Session-Key`

This endpoint expects `messages` to contain the current user turn only. The server reads only the
last message entry and treats it as the exact latest user message, plus optional UI metadata in
`context`.

Conversation continuity comes from the backend agent session key. When `newChat` is `false`,
the client must send `runtime_session_id` to resume the existing session.
If the request includes an explicit `runtime_session_id`, the server resumes that session even when
the request still arrives with `newChat: true`.
`threadId` is informational for the frontend when backend registration is enabled and does not
control which session is resumed.
For `mainsequence-project-coder`, the first `newChat: true` request must also include `projectId`
and `cwd`. Resume requests can omit them when the session metadata already contains those values.
When that coder session starts, the runtime emits the deterministic project bootstrap as synthetic
tool events before normal Pi output begins.
If the latest user message contains the word `MOCK`, the stream returns a synthetic response
immediately and skips agent execution, session creation, and conversation persistence.

The stream wrapper injects:

- `system` as an optional prompt prefix
- `context` as structured UI context
- `tools` as optional UI tool metadata
- optional lightweight `model` selection metadata
- only the last `messages` entry as the turn input
- `newChat` as the initial signal to create a new backend AgentSession when the thread does not
  already point at an active session

Response headers include:

- `Content-Type: text/event-stream`
- `X-Stream-Protocol: ui-message-stream`
- `X-Agent-Id` when the backend returned an Agent `id` for the thread

The stream ends with a final `data: [DONE]` marker after the `finish` or `error` chunk.

Each stream chunk now has this envelope:

```json
{
  "type": "text-delta",
  "textDelta": "hello",
  "agent_id": 123
}
```

When the orchestrator requests a real switch into a checked-out project session, the stream emits:

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
    "summary": "Project setup is complete and the coder session is ready."
  },
  "agent_id": 456
}
```

After that `session_switch`, the same SSE response may continue with
`mainsequence-project-coder` chunks for the new session:

- `new_session`
- a short assistant runtime status message
- deterministic bootstrap tool events such as `runtime_mainsequence_project_sdk_status`
- either a ready message or continued coder output

### `POST /api/a2a/chat`

Receives machine-facing A2A requests on the Astro streamer and returns the response on the same SSE
connection.

This endpoint normalizes an A2A JSON request into the normal stream pipeline while deterministically
injecting A2A execution context. At minimum, the injected contract tells the target runtime:

- this is agent-to-agent communication
- this is not a human-facing chat request
- the requested response format must be followed exactly

Current request fields accepted by Astro include:

```json
{
  "newChat": true,
  "userId": "user_123",
  "agentName": "mainsequence-project-executor",
  "task": "Inspect the prepared project and summarize the next implementation step.",
  "response_format": "Return a concise machine-facing status summary with blockers and next actions.",
  "caller": {
    "agent_name": "astro-orchestrator"
  }
}
```

Astro also accepts task aliases such as `message`, `input`, `prompt`, or `request`.

### `POST /api/a2a/cancel`

Requests cancellation of an active A2A-backed runtime session. This is an out-of-band control path,
not part of the streamed `POST /api/a2a/chat` response itself.

### `GET /api/chat/history?sessionId=<runtime_session_id>`

Returns the compact JSON conversation snapshot for an existing session. This endpoint is intended
for fast chat hydration and does not replay the live SSE stream. Astro owns this response shape:
it returns local `.history.json` when available, otherwise it fetches the backend `AgentSession`
and latest checkpoint, projects `bundle.pi_session_jsonl` into frontend user/assistant messages,
normalizes assistant thinking into structured `reasoning` content parts, and caches the rebuilt
`.history.json` locally.

### `GET /api/chat/diff?sessionId=<runtime_session_id>`

Returns the current deterministic git diff snapshot for a `mainsequence-project-coder` session.
The frontend only supplies the runtime session id; the server resolves the frozen repo root from
session metadata and returns the current patch plus a file summary.

### `GET /api/chat/session-tools?sessionId=<runtime_session_id>`

Returns the deterministic backend tools advertised for the runtime session. The initial use case is
`repo_diff`, which points to the relative `GET /api/chat/diff?...` URL for the same session.
When no deterministic tools are available, or local metadata is not available yet, the response is
still `200` with `available_tools: {}`.

### `GET /api/chat/session-model?sessionId=<runtime_session_id>`

Returns the model binding stored for the runtime session.

### `PATCH /api/chat/session-config`

Updates the editable subset of session-local config. The current read contract remains
the backend-owned session insights projection; this patch endpoint only accepts writable fields and
returns a minimal acknowledgement.

Initial writable fields:

- `config.compaction.enabled`
- `config.compaction.reserveTokens`

### `GET /api/model-providers?userId=<user_id>`

Returns backend-owned auth state for managed auth-backed model providers Astro currently exposes in
the UI, such as `openai`, `anthropic`, `openai-codex`, and `github-copilot`.

### `POST /api/model-providers/:provider/signin`

Starts provider signin. Immediate providers return `200`; interactive providers return `202` with
an attempt object. The JSON body must include `userId` or one of Astro's accepted user-id aliases.

### `GET /api/model-providers/:provider/signin/:attemptId`

Returns the current state of an interactive signin attempt.

### `POST /api/model-providers/:provider/signin/:attemptId/manual`

Submits pasted callback input for an interactive signin attempt.

### `POST /api/model-providers/:provider/signin/:attemptId/cancel`

Cancels an active interactive signin attempt.

### `POST /api/model-providers/:provider/signoff`

Revokes the provider credential in the backend for the requested user.

### `GET /api/models/catalog`

Returns Astro's global model catalog from the Pi registry without runtime availability filtering,
but still filtered to the providers Astro currently supports in-product. Pass `userId` to annotate
auth-backed entries with user-scoped backend credential status.

### `GET /api/chat/get_available_models`

Returns the models Astro can currently offer through its model collectors without sending a message
to Pi. Pass `userId` to evaluate auth-backed provider availability for that user.

The response is grouped by `provider`, with each provider carrying its own `models` array.

Current sources:

- Pi's actual available model registry, filtered to Astro-supported providers
- Ollama models discovered from `OLLAMA_HOST` with `/api/tags` plus per-model `/api/show`, when configured

The response is source-aware and supports partial failure.
If `OLLAMA_HOST` is not configured, the Ollama source is omitted.
If Ollama discovery fails after `OLLAMA_HOST` is configured, the endpoint can still return
Pi-registry-backed models plus an error entry for the Ollama source.
Auth-backed Pi models only appear here when they are executable right now. Known-but-signed-off
models remain visible in `GET /api/models/catalog` instead.

## Session storage

When a new session is created, the stream emits a `new_session` chunk before `start`.

Session files are stored at:

`ASTRO_STREAM_SESSION_DIR/<agent_session_id>.jsonl`
`ASTRO_STREAM_SESSION_DIR/<agent_session_id>.meta.json`

Frontend chat history shape is owned by Astro and keyed by `AgentSession.id` and `thread_id`.
Local `.conversation.jsonl` and `.history.json` files are process-local cache. If the full local
history snapshot is missing, `GET /api/chat/history` reconstructs the transcript from backend
`AgentSession` fields plus the latest checkpoint `bundle.pi_session_jsonl`. The backend does not
store Astro's frontend-history response blob. Pi `thinking` blocks and literal provider
`<think>...</think>` text are returned as structured `reasoning` parts, not as normal assistant
text.

### `GET /health`

Returns the current stream process health plus recent captured runtime issues.
The endpoint stays HTTP 200 while the process is alive, even when `status` is `degraded`, so
platform health probes do not restart the pod just because a recoverable request/runtime error was
captured.

```json
{
  "ok": true,
  "status": "ok",
  "degraded": false,
  "pid": 123,
  "startedAt": "2026-04-20T12:00:00.000Z",
  "lastUpdatedAt": "2026-04-20T12:01:00.000Z",
  "uptimeSeconds": 60,
  "healthStatePath": "/home/appuser/.astro-container-data/.astro/stream-health.json",
  "issueCount": 0,
  "recentIssues": [],
  "previousRun": null
}
```

Captured issues are also persisted to `ASTRO_STREAM_HEALTH_STATE_PATH` when set, otherwise to
`ASTRO_CONTAINER_DATA_DIR/.astro/stream-health.json` in the deployable container.
The stream records startup bootstrap failures, uncaught exceptions, unhandled promise rejections,
top-level request failures, HTTP server/client errors, and failed Pi child processes there instead
of intentionally exiting the whole stream process.

## Logging

Traffic logging is enabled by default. Disable with:

```bash
ASTRO_STREAM_LOG_TRAFFIC=0
```
