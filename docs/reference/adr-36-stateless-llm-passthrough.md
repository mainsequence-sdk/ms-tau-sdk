# ADR 36: Stateless LLM Passthrough Endpoint

Status: Accepted
Date: 2026-06-16
Implementation Status: Partially implemented

## Context

Astro now has two different runtime concerns:

- attached A2A session runtimes, which preserve backend session semantics, checkpoints, history,
  capabilities, credentials, and Pi runtime state
- simple machine calls that only need a direct LLM answer and do not need an agent session at all

The attached runtime protocol in ADR 35 is correct for session-backed agent work, but it is the wrong
shape for "ask a model for a fast JSON answer." That request should not pay for checkpoint leases,
session metadata validation, Pi runner startup, capability materialization, project attachment, or
session-history writes.

## Problem

Using an attached A2A runtime for stateless LLM calls creates unnecessary latency and confusing
semantics:

- the caller must provide or create a backend `agent_session_uid` even though no session memory is
  needed
- Astro may run checkpoint/session preparation even though no durable state should be written
- Pi runner startup can dominate the request cost for a simple model call
- session output options and runtime lifecycle concerns leak into a request that should be plain
  inference
- failures can be misreported as runtime/session failures when the operation is only an LLM call

The system needs a separate fast path with a contract that makes the absence of session state
explicit.

## Decision

Astro will add a stateless LLM passthrough endpoint.

This endpoint is not an A2A session runtime and is not an agent execution path. It is a direct
request/response inference API that resolves an allowed provider/model, calls the model provider,
enforces requested output controls, and returns the result.

The default endpoint returns a normal HTTP JSON response. This is a core part of the contract, not an
optional client-side normalization step.

The endpoint must not:

- require `agent_session_uid`
- accept `thread_id` as durable conversation identity
- attach to or create a runtime
- start or reuse a Pi runner
- acquire or renew checkpoint leases
- restore or write checkpoints
- create, read, or write session history
- materialize session capabilities or agent skills
- attach a project workspace
- queue behind a session runtime
- write backend session metadata

The endpoint may still:

- authenticate and authorize the caller
- resolve provider credentials for the caller or configured runtime account
- enforce provider/model allowlists
- apply request-level output controls
- validate and repair strict JSON responses
- return usage, finish reason, and structured errors
- optionally stream provider output when the caller asks for streaming

## REST Contract

### Non-Streaming LLM Chat

```http
POST /api/llm/chat
```

This is the primary endpoint. It always responds with `application/json`.

Request:

```json
{
  "provider": "openai-codex",
  "model": "gpt-5.3-codex-spark",
  "messages": [
    {
      "role": "user",
      "content": "Return JSON with only 2 string keys."
    }
  ],
  "response_format": {
    "type": "json_object",
    "strict": true
  },
  "json_repair": {
    "attempts": 3
  },
  "omit_reasoning": true,
  "timeout_seconds": 120
}
```

Response:

```json
{
  "ok": true,
  "provider": "openai-codex",
  "model": "gpt-5.3-codex-spark",
  "message": {
    "role": "assistant",
    "content": "{\"key1\":\"value1\",\"key2\":\"value2\"}"
  },
  "json": {
    "key1": "value1",
    "key2": "value2"
  },
  "finish_reason": "stop",
  "usage": {
    "input_tokens": 42,
    "output_tokens": 18
  }
}
```

Rules:

- `messages` is the complete context for the call.
- The request body has one canonical shape. Do not accept `message`, `prompt`, `input`, camelCase
  option aliases, or alternate token-limit field names.
- The endpoint must not load prior messages from Astro or the backend.
- The endpoint must not persist the request or response as conversation history.
- The HTTP response body must be JSON, not SSE and not raw model text.
- `timeout_seconds` is a request timeout for the provider call only.
- Strict JSON mode reuses the ADR 34 validation and repair semantics.
- When strict JSON is enabled and validation succeeds, `json` contains the parsed JSON value.
- When strict JSON is not enabled, `json` is omitted.
- `message.content` contains the final assistant text.
- Reasoning suppression controls the response surface only; it does not create a session or runtime.

### Streaming LLM Chat

```http
POST /api/llm/chat/stream
```

Request:

```json
{
  "provider": "openai-codex",
  "model": "gpt-5.3-codex-spark",
  "messages": [
    {
      "role": "user",
      "content": "Summarize this payload in one sentence."
    }
  ],
  "omit_reasoning": true,
  "timeout_seconds": 120
}
```

Response transport: SSE.

Rules:

- Streaming is an explicit endpoint, not a `stream` flag on the JSON endpoint.
- The streaming endpoint follows the same stateless rules as `POST /api/llm/chat`.
- Strict JSON over streaming must buffer assistant text until validation succeeds, matching ADR 34.
- Non-strict streaming may forward text deltas as they arrive from the provider.

## Request Validation

The stateless endpoint must reject session/runtime fields.

These session/runtime fields are invalid:

- `agent_session_uid`
- `agentSessionUid`
- `thread_id`
- `threadId`
- `agent_type`
- `agentType`
- `runtime_turn_timeout_seconds`
- `runtimeTurnTimeoutSeconds`

These non-canonical request aliases are also invalid:

- `message`
- `prompt`
- `input`
- `responseFormat`
- `jsonRepair`
- `omitReasoning`
- `timeoutSeconds`
- `baseUrl`
- `topP`
- `max_tokens`
- `maxOutputTokens`
- `maxTokens`

Invalid request response:

```json
{
  "ok": false,
  "error": "invalid_llm_passthrough_request",
  "message": "Stateless LLM passthrough requests must not include session or runtime fields."
}
```

## Fast Path Requirements

The implementation must keep the request path small and explicit:

```text
parse request
authenticate caller
resolve allowed provider/model
resolve provider credential
call provider
validate/repair output when requested
return response
```

The implementation must not call the session-runtime path and then skip parts of it. This endpoint
should be a separate handler/service so that checkpoint, Pi, capability, and history code cannot
accidentally enter the request path.

Credential resolution should use a provider credential cache that is independent of session state.
Cache misses are allowed, but they must be measured separately from provider latency so slow
credential lookup is visible.

## Observability

The endpoint should log timing for each major phase:

- request validation
- authentication
- provider/model resolution
- credential resolution
- provider first token or first response byte
- provider completion
- JSON validation and repair

The logs must not imply that a session runtime was involved.

Recommended events:

- `llm_passthrough_request_started`
- `llm_passthrough_model_resolved`
- `llm_passthrough_credentials_resolved`
- `llm_passthrough_provider_first_output`
- `llm_passthrough_json_repair_attempted`
- `llm_passthrough_request_completed`
- `llm_passthrough_request_failed`

## Error Contract

Errors should be plain LLM passthrough errors, not session/runtime errors.

Examples:

```json
{
  "ok": false,
  "error": "provider_credentials_unavailable",
  "message": "No usable credentials are available for provider openai-codex."
}
```

```json
{
  "ok": false,
  "error": "llm_passthrough_timeout",
  "message": "The provider call exceeded timeout_seconds."
}
```

```json
{
  "ok": false,
  "error": "llm_invalid_json_response",
  "message": "The model did not produce valid JSON after repair attempts."
}
```

## Relationship To Other ADRs

ADR 35 remains the contract for session-backed A2A runtime work. Use ADR 35 when the caller needs
agent continuity, checkpoint safety, capabilities, tools, project workspace state, or multiple turns
against the same live runtime.

ADR 36 is for stateless inference only. The caller owns all context by sending `messages` on every
request.

ADR 34 output controls apply to both paths, but ADR 36 must enforce them without creating or using a
session runtime.

## Non-Goals

- Do not create a lightweight session system.
- Do not persist transcript, request, response, checkpoint, or runtime state.
- Do not expose tools, capabilities, skills, project files, or workspace execution.
- Do not reuse Pi as an implementation detail for this fast path.
- Do not add backend session creation or lookup.
- Do not make this endpoint a replacement for attached A2A runtimes.

## Implementation Plan

- [x] Add `POST /api/llm/chat` as a non-streaming JSON endpoint.
- [ ] Add `POST /api/llm/chat/stream` as an explicit SSE endpoint if streaming passthrough is
  required.
- [x] Implement a separate stateless LLM service that does not import or call session-runtime,
  checkpoint, Pi runner, capability materialization, or conversation-store code.
- [x] Reject all session/runtime identity fields in passthrough requests.
- [x] Enforce one canonical request body shape and reject shortcut/camelCase aliases.
- [x] Resolve provider/model through explicit OpenAI-compatible passthrough provider mapping and
  credential-backed provider definitions.
- [x] Resolve provider credentials independently of backend session state, using environment
  credentials first and backend model-provider credential hydration with `agent_session_uid: null`
  when needed.
- [x] Reuse ADR 34 strict JSON validation and repair logic.
- [x] Return parsed `json` on successful strict JSON responses.
- [x] Add structured passthrough errors for validation, credentials, provider failure, timeout, and
  invalid JSON.
- [x] Add timing logs that prove the fast path skipped checkpoints, Pi startup, capabilities, and
  persistence.
- [x] Add focused tests for session/runtime field rejection, JSON response shaping, and strict JSON
  repair.
- [x] Add API documentation for stateless LLM calls.
- [ ] Add a Python client helper for stateless LLM calls.
