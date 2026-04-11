# Session Start Plan (Agent-Spawning Rules)

This plan defines how the HTTP stream backend validates agents, registers them, and starts backend AgentSessions. It reflects the request contract that includes `agentName`, `userId`, and `newChat`.

## Notes From Review

- `newChat: false` must include a `runtime_session_id`; otherwise return an HTTP error.
- `input_text` and `output_text` are removed from the AgentSession payload contract.
- `runtime_session_id` is required for session tracking and must be stored in metadata.

## Goals

1. Enforce a strict agent registry (reject unknown agent names).
2. Use deterministic backend Agent registration: `{agent_name}_{user_id}`.
3. Create a backend AgentSession only when the frontend declares `newChat: true`.
4. Persist local session files per backend Agent unique id and session key.

## Request Contract (Frontend -> Backend)

Required fields:

- `agentName` (string)
- `userId` (string | number)
- `newChat` (boolean)
- `messages` (array; last element is the latest user message)
- `threadId` (string; client bookkeeping only)

Conditional fields:

- `runtime_session_id` (string) is required when `newChat: false`.

Optional fields:

- `system`, `context`, `tools` (as today)
- `sessionMetadata` (object) to map into `session_metadata` in the AgentSession payload

## Agent Registry Enforcement

1. Maintain a deterministic registry of allowed agent names (initially:
   - `astro-orchestrator`
   - additional agent names added explicitly as needed).
2. On each request:
   - if `agentName` is missing or not in the registry -> return `400` with `error: unknown_agent`.

## Backend Agent Registration

1. Build `agent_unique_id = "{agent_name}_{user_id}"`.
2. Call `Agent.get_or_create` via:
   - `/orm/api/agents/v1/agents/get_or_create/`
3. Persist the returned `agentId` and `agent_unique_id` in a metadata file keyed by the agent unique id.

## Session Creation Rules

If `newChat: true`:

1. Call `/orm/api/agents/v1/agents/{agentId}/start_new_session/`.
2. Use the payload described below.
3. Generate a new `runtime_session_id` (local session key).
4. Save the backend `AgentSession` id and `runtime_session_id` into local metadata.
5. Start a new local Pi session file for this AgentSession.

If `newChat: false`:

1. Require `runtime_session_id` in the request.
2. Validate that a local session exists for that `runtime_session_id`.
3. If no session exists, return an error (see Failure Handling).
4. Do not call `start_new_session/`.

## AgentSession Payload Mapping

When starting a new session:

- `status`: `"running"`
- `started_at`: current UTC timestamp
- `ended_at`: `null`
- `created_by_user`: the incoming `userId`
- `llm_provider`: set from runtime config (default `"openai"`)
- `llm_model`: set from runtime config (default `"gpt-5.4"`)
- `engine_name`: `"astro_router_v1"`
- `runtime_config_snapshot`: serialize current inference settings (temperature/top_p/max_output_tokens/reasoning_effort)
- `error_detail`: `""`
- `external_session_id`: set only if the LLM provider returns one, otherwise `""`
- `runtime_session_id`: local session key (required)
- `thread_id`: the incoming `threadId` or a generated one
- `usage_summary`: initialize to zeros
- `session_metadata`: map from request `sessionMetadata` plus standard tags like:
  - `source: "frontend"`
  - `workflow_key: agentName`

## Local Session Key Strategy

Use backend Agent identity as the root key, then append a session counter for multiple sessions:

```
session_key = "<agent_unique_id>__session_<n>"
```

Where `<n>` increments each time `newChat: true` starts a new session for the same agent.

Local session files live at:

```
ASTRO_STREAM_SESSION_DIR/<session_key>.jsonl
ASTRO_STREAM_SESSION_DIR/<session_key>.meta.json
```

Metadata should store:

- `agentId`
- `agentUniqueId`
- `agentSessionId` (backend)
- `sessionKey` (runtime_session_id)
- `startedAt`
- `threadId`

## Response Headers and Stream Metadata

Include:

- `X-Thread-Id` (thread bookkeeping)
- `X-Agent-Id` (backend Agent id)
- `X-Agent-Unique-Id` (deterministic identity)
- `X-Agent-Session-Id` (backend AgentSession id when created)
- `X-Session-Key` (local session key)

Each SSE chunk should keep `agent_id`.

## Failure Handling

- Unknown agent: `400` with `error: unknown_agent`.
- Missing `runtime_session_id` when `newChat: false`: `400` with `error: missing_runtime_session_id`.
- `runtime_session_id` provided but no local session exists: `409` with `error: session_not_found`.
- Agent registration failure: `502` with error details.
- Session creation failure on `newChat`: `502` with error details and abort the request.

## Where This Lives

- Implementation in `interface/stream/server.ts`.
- Shared helpers for registry lookup and session key derivation in a new
  `interface/stream/session.ts` (or `pi/extensions/shared/session.ts` if we want reuse).

## Strategy Summary

1. Validate `agentName` against a registry.
2. Resolve backend Agent (`get_or_create`) using deterministic `{agent_name}_{user_id}`.
3. If `newChat: true`, start backend AgentSession and create a new local session file.
4. If `newChat: false`, require `runtime_session_id` and reuse the existing local session.
