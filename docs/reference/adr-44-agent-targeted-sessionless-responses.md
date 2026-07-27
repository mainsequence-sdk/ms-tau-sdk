# ADR 44: Agent-Targeted Sessionless Responses

Status: Accepted
Date: 2026-07-26
Implementation Status: Not implemented

## Context

Astro currently exposes two durable agent execution paths and one stateless model path:

- `POST /api/chat` attaches to an existing backend `AgentSession` and preserves conversation
  entries, checkpoints, session capabilities, and runtime state.
- A2A `message:send` and `message:stream` use `message.contextId = AgentSession.uid` and persist
  task and message state.
- `POST /api/llm/chat`, defined by ADR 36, is a direct model passthrough. It deliberately does not
  resolve an agent, load agent defaults, materialize capabilities, or attach project context.

These contracts do not cover a common request: ask a particular agent one question, optionally
provide a PDF, document, or image, use the agent's configured model and applicable capabilities,
and return the answer without creating or recording a conversation session.

Routing this request through Chat or A2A creates durable state that the caller did not request.
Routing it through `/api/llm/chat` loses the agent identity, defaults, instructions, and
capabilities that the caller did request.

## Decision

Astro will add a third execution mode: an agent-targeted, sessionless response.

The public endpoints are:

```http
POST /api/agents/{agent_uid}/responses
POST /api/agents/{agent_uid}/responses/stream
```

The non-streaming endpoint returns one JSON response. The streaming endpoint returns SSE.

Both endpoints:

- require a public backend `Agent.uid` in the path
- authenticate the caller and authorize access to that agent
- resolve an immutable execution snapshot for that agent
- allow request-level provider, model, and thinking overrides
- fall back to the agent's defaults when overrides are absent
- use only capabilities that explicitly support sessionless execution
- accept supported text and file parts
- do not create or attach to an `AgentSession`
- do not create an A2A `AgentTask`
- do not persist a conversation transcript, checkpoint, or raw attachment

This is an agent execution path, not an extension of ADR 36's model passthrough.

## Endpoint Identity And Authorization

`agent_uid` is required in the URL. It must not be inferred from a model name, agent type, project,
display name, or request metadata.

The request body must not contain a second `agent_uid`. Keeping the identity only in the path avoids
conflicting identities.

The verified caller must be authorized to use the requested agent. For an Astro deployment bound to
one backend agent, the requested UID must match the runtime credential's bound agent. A central
gateway may route to multiple agents, but it must authorize each requested UID before resolving
configuration or credentials.

A caller-supplied `X-MainSequence-User-Uid` is not sufficient authority. User identity must come
from a verified bearer/runtime credential or from a trusted gateway that strips and replaces
identity headers.

Unknown agents return `404`. Known but unauthorized agents return `403` without exposing agent
configuration.

## Agent Execution Snapshot

The backend remains the source of truth for the agent. Before inference, Astro resolves a bounded,
immutable snapshot containing at least:

- `agent_uid`
- agent name and description
- runtime harness compatibility
- default provider
- default model
- default thinking level
- agent prompt/instruction capabilities
- agent capabilities eligible for sessionless execution
- capability content versions or hashes
- allowed input media types

The snapshot must be resolved without creating an `AgentSession`. Astro must not synthesize a
temporary session row as an implementation shortcut.

The backend may expose the snapshot through one dedicated endpoint or through agent and capability
reads. A dedicated snapshot endpoint is preferred because it keeps the default-resolution and
authorization rules aligned with session creation.

If the agent is not compatible with the deployed harness, Astro returns
`agent_harness_not_supported`.

## Provider, Model, And Thinking Resolution

The request may specify:

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-5",
  "thinking": "high"
}
```

All three fields are optional. Resolution follows the same backend-owned agent defaults used when a
session is initialized, without creating a session:

1. Resolve the agent's default provider, model, and thinking configuration.
2. Overlay request fields that are explicitly present.
3. Validate the effective provider/model pair against the provider catalog and the caller's
   allowed credentials.
4. Validate the effective thinking level against the effective model.

The detailed precedence rules are:

| Request | Effective selection |
| --- | --- |
| no provider, model, or thinking | agent default provider, model, and thinking |
| model only | requested model under the agent default provider |
| thinking only | agent default provider/model with requested thinking |
| provider and model | requested provider/model |
| provider, model, and thinking | all requested values |
| provider only, different from agent default | rejected because a compatible model is ambiguous |

Omitted thinking inherits the agent default thinking value, matching the session snapshot rule. If
the agent default thinking value is blank, Astro uses the provider catalog's default for the
effective model.

The inherited thinking value is still validated against the effective provider/model pair. If the
caller changes provider or model and the non-blank agent default thinking value is incompatible,
Astro rejects the selection and requires an explicit compatible thinking override. It must not
silently change an explicit or non-blank agent setting.

An explicit `"thinking": "off"` is an override and must not be treated as an omitted value.

If the agent has no complete default provider/model pair and the request does not supply a complete
pair, Astro returns `agent_model_not_configured`.

The response reports the effective provider, model, and thinking level, plus whether each value came
from `request`, `agent_default`, or `provider_catalog_default`.

Request overrides affect only the current response. They must not update the agent or any existing
session.

## Request Contract

The request contains `parts` plus optional execution and output controls:

```json
{
  "parts": [
    {
      "text": "Summarize the risks in this report."
    },
    {
      "raw": "JVBERi0xLjQK...",
      "filename": "report.pdf",
      "mediaType": "application/pdf"
    }
  ],
  "provider": "openai",
  "model": "gpt-5.4",
  "thinking": "medium",
  "response_format": {
    "type": "text"
  },
  "max_output_tokens": 1200,
  "timeout_seconds": 120,
  "metadata": {
    "request_id": "client-generated-id"
  }
}
```

The part shapes reuse standard A2A text and file-part fields so Astro can share one normalization
and validation implementation. Reusing the part shape does not make this an A2A request and does
not introduce `contextId`, `messageId`, or `taskId`.

Rules:

- At least one non-empty text or supported file part is required.
- `contextId`, `agent_session_uid`, `session_uid`, `taskId`, and `thread_id` are rejected.
- File parts initially use inline `raw` bytes. URL fetching remains disabled until its SSRF and
  download controls are implemented.
- The request is complete context for this response. Prior session or one-shot requests are never
  loaded.
- `response_format` may request plain text or the existing strict JSON behavior.
- Request metadata must not change identity, provider credentials, capability eligibility, or
  persistence behavior.

## Response Contract

The JSON endpoint returns:

```json
{
  "ok": true,
  "request_id": "client-generated-id",
  "agent_uid": "b73bed96-86fa-4c65-8df5-c96eca314a06",
  "resolved_model": {
    "provider": "openai",
    "model": "gpt-5.4",
    "thinking": "medium",
    "sources": {
      "provider": "request",
      "model": "request",
      "thinking": "request"
    }
  },
  "message": {
    "role": "assistant",
    "content": "The primary risks are..."
  },
  "finish_reason": "stop",
  "usage": {
    "input_tokens": 1250,
    "output_tokens": 280
  },
  "capabilities_used": [
    "pdf-text-extraction"
  ]
}
```

The response must not include a session UID, task ID, checkpoint version, or continuation token.
The caller starts another independent response request for subsequent work and resends any required
context.

The SSE endpoint follows the same resolution, authorization, capability, attachment, and
non-persistence rules. It must cancel provider/tool work and clean up request files when the client
disconnects.

## Sessionless Capability Policy

Agent capabilities are not automatically safe merely because they are bound to an agent. Capability
metadata must be able to express:

- whether sessionless execution is allowed
- whether the capability requires a session or checkpoint
- whether it can mutate project, platform, or external state
- supported input media types
- required tools or provider modalities
- execution timeout and turn limits

The default one-shot profile is read-only. Prompt capabilities, read-only extraction, read-only web
access, and read-only MCP tools may be enabled when explicitly declared sessionless-safe.

Write/edit/bash tools and mutating MCP or external actions are excluded by default. A capability
that requires durable continuity, user confirmation, a lease, or a mutation audit must return
`capability_requires_session` and direct the caller to Chat or A2A.

The runtime must use enough model/tool turns to complete enabled tools. The one-turn, no-tool
configuration used by ADR 36 is not sufficient for this endpoint.

## File And Multimodal Handling

An input is supported only when every required layer supports it:

```text
request parser
  + agent sessionless capability
  + extractor or native model modality
  + provider adapter serialization
```

The presence of an agent capability alone is not enough.

Initial recommended behavior:

| Media type | Behavior |
| --- | --- |
| `text/plain`, Markdown, JSON, CSV | validate, decode with bounded text extraction |
| `application/pdf` | extract embedded text through a declared read-only capability |
| JPEG, PNG, WebP | use native vision only when the model and provider adapter support it |
| DOCX | accept only when a declared document extraction capability is installed |
| PPTX, XLSX, OCR-only PDF, audio | reject until a matching capability is installed |

Unsupported media must produce an explicit per-part validation error. Astro must never silently
discard an image, document, data part, or file.

File intake must enforce:

- allowed MIME types
- MIME/content signature validation
- safe filenames
- per-file, aggregate-byte, and file-count limits
- bounded decompression and parsing
- encrypted/scanned-document errors
- no raw base64 or extracted document contents in logs

Files are materialized into a request-scoped directory with owner-only permissions. They are
deleted in a `finally` path after success, error, timeout, cancellation, or stream disconnect.
They are not written into session assets, the project checkout, checkpoints, or backend task
messages.

## Persistence Boundary

"Sessionless" means Astro and the backend do not persist:

- an `AgentSession`
- Tau entries or checkpoints
- an `AgentTask`, task message, output, or event
- prompt or response contents as conversation history
- raw attachment bytes
- extracted document text

The platform may retain bounded operational records needed for authentication, billing, abuse
prevention, and reliability:

- request/correlation ID
- authenticated user and agent UID
- resolved provider/model/thinking and their resolution sources
- capability snapshot version and capabilities used
- attachment filename, MIME type, byte size, and digest
- token usage, latency, status, and structured error code

Prompt, response, raw file, and extracted file contents are excluded from operational logs by
default. Any diagnostic content logging must be separately controlled, redacted, and retention
bounded.

## Credential Resolution

Credential hydration must support an authenticated user and authorized `agent_uid` without requiring
`agent_session_uid`.

The backend contract should either:

- allow `agent_session_uid: null` while requiring `agent_uid`, or
- expose a dedicated sessionless agent credential-hydration operation.

The backend must verify that the runtime and user may execute the selected provider/model for the
requested agent. Astro must not create a session only to satisfy the credential API.

Credential refresh, when supported by a provider, must preserve the same authenticated user,
agent UID, provider, and request authorization.

## Errors

Errors use stable JSON codes for both JSON responses and terminal SSE events:

- `agent_not_found`
- `agent_forbidden`
- `agent_harness_not_supported`
- `agent_model_not_configured`
- `invalid_model_override`
- `provider_credentials_unavailable`
- `thinking_level_not_supported`
- `unsupported_input_media_type`
- `input_capability_unavailable`
- `capability_requires_session`
- `attachment_too_large`
- `attachment_processing_failed`
- `agent_response_timeout`
- `agent_response_failed`

Validation failures are `400` or `422`, authorization failures are `401` or `403`, unsupported
media is `415`, and unavailable sessionless capabilities may use `409` when Chat or A2A is the
required alternative.

## Observability

Recommended events:

- `agent_response_request_started`
- `agent_response_snapshot_resolved`
- `agent_response_model_resolved`
- `agent_response_attachment_validated`
- `agent_response_capabilities_resolved`
- `agent_response_provider_first_output`
- `agent_response_request_completed`
- `agent_response_request_failed`
- `agent_response_cleanup_completed`

Timing should separate authentication, agent snapshot resolution, credential resolution, attachment
processing, capability execution, provider first output, and provider completion.

Logs must make it explicit that no session or task was created.

## Relationship To Existing Endpoints

Use `/api/chat` when the user wants conversation continuity, durable agent state, full session
capabilities, or mutating project/platform work.

Use A2A when another agent needs a durable task, task status, artifacts, continuation, or standard
A2A interoperability.

Use `/api/llm/chat` from ADR 36 when the caller wants raw model inference and owns the full prompt,
provider, and model context. ADR 36 remains model-only and must not start resolving agents or
capabilities.

Use `/api/agents/{agent_uid}/responses` when the caller wants a configured agent for one independent
response without a recorded session.

## Consequences

Positive:

- callers can use agent defaults and safe capabilities without manufacturing a session
- model and thinking overrides remain available per request
- one-shot document and image questions gain explicit capability and modality validation
- Chat, A2A, raw model inference, and one-shot agent inference have distinct persistence semantics

Costs:

- the backend needs sessionless agent snapshot and credential support
- capabilities need sessionless and mutation metadata
- provider adapters need real multimodal serialization before image support can be advertised
- request-scoped attachment extraction and cleanup require dedicated tests
- client applications need a separate "Ask once" flow

## Non-Goals

- Do not create an ephemeral or hidden backend session.
- Do not preserve conversation memory between response requests.
- Do not make all agent capabilities available outside a session.
- Do not permit silent fallback to another agent, provider, model, or media-processing strategy.
- Do not change the agent's persisted defaults when applying request overrides.
- Do not replace Chat, A2A, or ADR 36.

## Implementation Plan

### Phase 1: Backend And Contract

- [ ] Add a backend agent execution snapshot contract keyed by `agent_uid`.
- [ ] Add sessionless, agent-authorized provider credential hydration.
- [ ] Define capability metadata for sessionless eligibility, mutations, and media types.
- [ ] Add typed Astro request, response, and error models.

### Phase 2: Text-Only Agent Responses

- [ ] Implement `POST /api/agents/{agent_uid}/responses`.
- [ ] Implement agent default and request override resolution.
- [ ] Compose the agent instructions and prompt capabilities from the immutable snapshot.
- [ ] Execute a text-only response without session, checkpoint, task, or transcript writes.
- [ ] Assert the non-persistence boundary in integration tests.

### Phase 3: Safe Capabilities And Attachments

- [ ] Add request-scoped attachment normalization, limits, and cleanup.
- [ ] Add bounded text and embedded-PDF-text extraction.
- [ ] Add sessionless-safe read-only capabilities with multi-turn tool execution.
- [ ] Add provider/model modality metadata and native image serialization.
- [ ] Add DOCX and other formats only behind installed capability declarations.

### Phase 4: Streaming And Clients

- [ ] Implement `POST /api/agents/{agent_uid}/responses/stream`.
- [ ] Cancel work and clean attachments on disconnect.
- [ ] Add client helpers and an "Ask once" UI with file validation.
- [ ] Document retry, timeout, and idempotency behavior.

### Phase 5: Verification

- [ ] Add endpoint tests for authentication, agent authorization, defaults, and every override
      combination.
- [ ] Add provider-credential integration tests with no session UID.
- [ ] Add attachment tests for supported, unsupported, malformed, oversized, encrypted, and
      cleanup cases.
- [ ] Add tests proving unsupported parts are never ignored.
- [ ] Add tests proving no session, checkpoint, task, transcript, or raw file is persisted.
