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
- `ASTRO_STREAM_SESSION_DIR` (default `<repo>/.astro/stream-sessions`)
- `ASTRO_STREAM_LOG_REQUEST_BODIES` (`1` enables request payload debug logging)

`ASTRO_STREAM_CORS_ORIGIN` is supported as a deprecated single-origin fallback, but the preferred
configuration is `ASTRO_STREAM_TRUSTED_ORIGINS`.

In `docker-compose.yml`, the stream service mounts the named volume `astro_container_data` to
`/root/.astro-container-data`, points runtime env at volume-backed paths such as
`/root/.astro-container-data/.pi/agent` and `/root/.astro-container-data/.astro/stream-sessions`,
and uses read-only legacy mounts only for one-time migration. That makes the container behave much
closer to a PVC-backed deployment.

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

### `GET /api/chat/history?sessionId=<runtime_session_id>`

Returns the compact JSON conversation snapshot for an existing session. This endpoint is intended
for fast chat hydration and does not replay the live SSE stream.

### `GET /api/chat/diff?sessionId=<runtime_session_id>`

Returns the current deterministic git diff snapshot for a `mainsequence-project-coder` session.
The frontend only supplies the runtime session id; the server resolves the frozen repo root from
session metadata and returns the current patch plus a file summary.

### `GET /api/chat/session-tools?sessionId=<runtime_session_id>`

Returns the deterministic backend tools advertised for the runtime session. The initial use case is
`repo_diff`, which points to the relative `GET /api/chat/diff?...` URL for the same session.

### `GET /api/chat/session-model?sessionId=<runtime_session_id>`

Returns the model binding stored for the runtime session.

### `GET /api/chat/session-insights?sessionId=<runtime_session_id>`

Returns one coherent runtime-session snapshot with:

- `session`
- `model`
- `usage`
- `context`
- `config`
- `editable`
- `lastTurn`

The `context` section includes both:

- `tokensRemainingBeforeCompaction`
- `tokensRemainingBeforeContextLimit`

The response also includes `config`, which exposes the effective:

- compaction policy
- reserve tokens
- compaction threshold
- model limits and reasoning effort

`editable` mirrors the writable subset of `config` and adds field-level editing metadata such as:

- `editable`
- `type`
- numeric `min` / `max` / `step`
- `unit`

### `PATCH /api/chat/session-config`

Updates the editable subset of session-local config. The current read contract remains
`GET /api/chat/session-insights`; this patch endpoint only accepts writable fields and returns a
minimal acknowledgement.

Initial writable fields:

- `config.compaction.enabled`
- `config.compaction.reserveTokens`

### `GET /api/model-providers`

Returns auth state for managed auth-backed model providers Astro currently exposes in the UI, such
as `openai`, `anthropic`, `openai-codex`, and `github-copilot`.

### `GET /api/storage/usage`

Returns a global storage snapshot for Astro's durable runtime root.

It reports:

- total filesystem/PVC capacity
- currently available bytes
- Astro-managed consumed bytes
- whether capacity is coming from the real filesystem or from `ASTRO_STORAGE_SIM_TOTAL_BYTES`
- a four-way bucket breakdown:
  - `pi`
  - `astro`
  - `sessions`
  - `system`

### `POST /api/model-providers/:provider/signin`

Starts provider signin. Immediate providers return `200`; interactive providers return `202` with
an attempt object.

### `GET /api/model-providers/:provider/signin/:attemptId`

Returns the current state of an interactive signin attempt.

### `POST /api/model-providers/:provider/signin/:attemptId/manual`

Submits pasted callback input for an interactive signin attempt.

### `POST /api/model-providers/:provider/signin/:attemptId/cancel`

Cancels an active interactive signin attempt.

### `POST /api/model-providers/:provider/signoff`

Signs a provider off from runtime auth state without changing the underlying environment variables.

### `GET /api/models/catalog`

Returns Astro's global model catalog from the Pi registry without runtime availability filtering,
but still filtered to the providers Astro currently supports in-product.

### `GET /api/chat/get_available_models`

Returns the models Astro can currently offer through its model collectors without sending a message
to Pi.

The response is grouped by `provider`, with each provider carrying its own `models` array.

Current sources:

- Pi's actual available model registry, filtered to Astro-supported providers
- Ollama models discovered from `OLLAMA_HOST` with `/api/tags` plus per-model `/api/show`, when configured

The response is source-aware and supports partial failure. If Ollama discovery fails, the endpoint
can still return Pi-registry-backed models plus an error entry for the Ollama source.
Auth-backed Pi models only appear here when they are executable right now. Known-but-signed-off
models remain visible in `GET /api/models/catalog` instead.

## Session storage

When a new session is created, the stream emits a `new_session` chunk before `start`.

Session files are stored at:

`ASTRO_STREAM_SESSION_DIR/<agent_session_id>.jsonl`
`ASTRO_STREAM_SESSION_DIR/<agent_session_id>.meta.json`
`ASTRO_STREAM_SESSION_DIR/<agent_session_id>.conversation.jsonl`
`ASTRO_STREAM_SESSION_DIR/<agent_session_id>.history.json`

### `GET /health`

Returns a simple status payload:

```json
{ "ok": true }
```

## Logging

Traffic logging is enabled by default. Disable with:

```bash
ASTRO_STREAM_LOG_TRAFFIC=0
```
