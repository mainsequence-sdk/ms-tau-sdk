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

- require the deployed agent's public `Agent.uid` in the path
- accept and return the canonical A2A `Message`, `Part`, configuration, metadata extension, and
  error shapes
- authenticate the caller locally from verified ingress/runtime identity
- resolve an immutable, deployment-provided local execution snapshot for that agent
- allow request-level provider, model, and thinking overrides
- fall back to the agent's defaults when overrides are absent
- use only capabilities that explicitly support sessionless execution
- accept supported text and file parts
- do not create or attach to an `AgentSession`
- do not create an A2A `AgentTask`
- do not persist a conversation transcript, checkpoint, or raw attachment

This is an agent execution path, not an extension of ADR 36's model passthrough.

## A2A Normalization Is The Source Of Truth

Astro must have one protocol-normalization core for agent messages. The A2A and sessionless
endpoints must share:

- typed `Message`, `Part`, and request-configuration models
- message role, message ID, text, data, raw file, filename, and media-type validation
- file signature, size, digest, and safe-name validation
- normalized text/data/file manifests
- `acceptedOutputModes` and the ADR 37 structured-output extension
- agent `Message` and `Part` response serialization
- A2A HTTP error envelopes and field-violation details
- SSE encoding for supported A2A message events

The sessionless endpoint must not define parallel top-level `parts`, `prompt`, `response_format`,
file-wrapper, output-message, or error models.

Shared normalization must be separated from execution side effects. The two adapters supply
different policies to the same core:

| Policy | Durable A2A | Agent-targeted sessionless response |
| --- | --- | --- |
| target agent | resolved by the A2A receiving endpoint | required URL `agent_uid` |
| `message.contextId` | required by the current durable implementation | forbidden |
| `message.taskId` | allowed for task continuation | forbidden |
| `message.messageId` | durable idempotency identity | required request identity |
| materialization root | session/message asset directory | request-scoped temporary directory |
| persistence | session, task, message, and bounded manifest | operational metadata only |
| successful result | direct `Message` or `Task` | direct `Message` only |

The sessionless handler must not call the durable A2A HTTP handler and then disable writes. It must
call the shared normalization, output-contract, serialization, and error components directly, then
dispatch through a sessionless execution adapter. This prevents session lookup and task persistence
from becoming accidental dependencies while preserving one source of truth for protocol data.

The endpoint is A2A-normalized but is not advertised as the standard A2A `message:send` operation.
The agent-scoped URL and the absence of `contextId` define a Main Sequence sessionless profile of
the A2A data model.

## Endpoint Identity And Authorization

`agent_uid` is required in the URL. It must not be inferred from a model name, agent type, project,
display name, or request metadata.

The request body must not contain a second `agent_uid`. Keeping the identity only in the path avoids
conflicting identities.

The verified caller must be authorized to use the requested agent without a backend lookup. For the
per-agent Astro deployment, the requested UID must match the UID in the locally mounted execution
snapshot and the runtime credential's locally available binding claims.

A caller-supplied `X-MainSequence-User-Uid` is not sufficient authority. User identity must come
from a verified bearer/runtime credential or from a trusted gateway that strips and replaces
identity headers.

The endpoint must verify signed token claims locally or trust an authenticated ingress. It must not
call a backend user, agent, session, or authorization endpoint during request handling.

An authenticated request for an agent UID other than the locally bound UID returns `404` without
revealing which other agents exist.

## Local Agent Execution Snapshot

The deployment control plane produces a bounded, immutable agent execution snapshot before the
Astro process starts. The snapshot is packaged in the runtime image or mounted as deployment
configuration and contains at least:

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

Agent prompt and capability content needed at execution time must also be packaged or mounted
locally. A manifest entry that points only to a backend capability is not usable by this endpoint.

Request handling reads and validates this local snapshot. It must not fetch the agent, Agent Card,
capability bindings, capability content, runtime configuration, model defaults, or project metadata
from the backend.

The deployment snapshot uses the same control-plane default-resolution code that initializes a
session, but the resolved values are delivered before request handling instead of being fetched by
the endpoint. Changes to agent defaults or capabilities require an explicit snapshot update and
runtime reload/redeployment. A request must not observe a partially updated snapshot.

The snapshot must be resolved without creating an `AgentSession`. Astro must not synthesize a
temporary session row as an implementation shortcut.

If the agent is not compatible with the deployed harness, Astro returns
`agent_harness_not_supported`.

## Provider, Model, And Thinking Resolution

The request may specify overrides through the declared Main Sequence A2A inference extension:

```json
{
  "metadata": {
    "https://mainsequence.ai/a2a/extensions/agent-inference/v1": {
      "provider": "anthropic",
      "model": "claude-sonnet-4-5",
      "thinking": "high"
    }
  }
}
```

All three fields are optional. Resolution follows the same agent-default semantics used when a
session is initialized, using the local deployment snapshot and without creating a session:

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

The request uses the same A2A `SendMessageRequest` envelope and `Part` objects as ADR 37, with
`contextId` omitted:

```http
POST /api/agents/{agent_uid}/responses
Content-Type: application/a2a+json
Accept: application/a2a+json
```

```json
{
  "message": {
    "messageId": "msg-8f6c3b38-8c13-4c7b-9b7a-98370c1889db",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "Summarize the risks in this report."
      },
      {
        "raw": "JVBERi0xLjQK...",
        "filename": "report.pdf",
        "mediaType": "application/pdf"
      }
    ]
  },
  "configuration": {
    "acceptedOutputModes": ["text/plain"],
    "responseKind": "message"
  },
  "metadata": {
    "https://mainsequence.ai/a2a/extensions/agent-inference/v1": {
      "provider": "openai",
      "model": "gpt-5.4",
      "thinking": "medium",
      "maxOutputTokens": 1200,
      "timeoutSeconds": 120
    }
  }
}
```

Provider, model, thinking, output-token, and timeout controls live in one declared metadata
extension instead of becoming endpoint-specific top-level fields. The same extension parser may be
used by durable A2A when that surface elects to allow per-turn inference overrides.

Rules:

- `message`, `message.messageId`, `message.role`, and `message.parts` follow ADR 37.
- `message.role` must be `ROLE_USER`.
- At least one non-empty text, data, or supported file part is required.
- `message.contextId` and `message.taskId` are rejected because their presence requests durable
  continuity.
- `agent_session_uid`, `session_uid`, and `thread_id` are rejected at every nesting level.
- File parts initially use inline `raw` bytes. URL fetching remains disabled until its SSRF and
  download controls are implemented.
- The request is complete context for this response. Prior session or one-shot requests are never
  loaded.
- Normal output negotiation uses `configuration.acceptedOutputModes`.
- Strict JSON uses the ADR 37
  `https://mainsequence.ai/a2a/extensions/output-contract/v1` metadata extension.
- `configuration.historyLength`, push notification configuration, and durable task controls are
  rejected.
- `message.messageId` is the request/correlation identity. Because no durable request record is
  created, it does not promise replay of a prior response after process termination.
- Undeclared metadata must not change identity, provider credentials, capability eligibility, or
  persistence behavior.

## Response Contract

The JSON endpoint returns the direct `Message` branch of the A2A `SendMessageResponse`. It never
returns a `Task`:

```json
{
  "message": {
    "messageId": "msg-agent-33841969-c7a4-48be-94c1-5a18d2bbef84",
    "role": "ROLE_AGENT",
    "parts": [
      {
        "text": "The primary risks are..."
      }
    ],
    "metadata": {
      "https://mainsequence.ai/a2a/extensions/agent-inference/v1": {
        "agentUid": "b73bed96-86fa-4c65-8df5-c96eca314a06",
        "resolved": {
          "provider": "openai",
          "model": "gpt-5.4",
          "thinking": "medium",
          "sources": {
            "provider": "request",
            "model": "request",
            "thinking": "request"
          }
        },
        "finishReason": "stop",
        "usage": {
          "inputTokens": 1250,
          "outputTokens": 280
        },
        "capabilitiesUsed": [
          "pdf-text-extraction"
        ]
      }
    }
  }
}
```

Text output uses `Part.text`. Structured JSON output uses `Part.data` with
`mediaType: "application/json"`, exactly as ADR 37. Model resolution, usage, and capability
telemetry live in the declared inference extension rather than a second custom response envelope.

The response omits `contextId` and must not include a session UID, task ID, checkpoint version, or
continuation token.
The caller starts another independent response request for subsequent work and resends any required
context.

The SSE endpoint follows the same resolution, authorization, capability, attachment, and
non-persistence rules and uses the shared A2A SSE encoder. It must not emit Astro/Tau-native model,
tool, or reasoning chunks. Until an A2A-compatible message-only incremental event is supported, it
buffers the answer and emits one final direct `Message` event. A future incremental-delta extension
must be declared and shared by both A2A adapters rather than invented only for this endpoint.

The streaming endpoint must cancel provider/tool work and clean up request files when the client
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

"Sessionless" means Astro does not ask the backend to persist:

- an `AgentSession`
- Tau entries or checkpoints
- an `AgentTask`, task message, output, or event
- prompt or response contents as conversation history
- raw attachment bytes
- extracted document text

Astro may emit bounded operational logs needed for authentication, billing, abuse prevention, and
reliability:

- request/correlation ID
- authenticated user and agent UID
- resolved provider/model/thinking and their resolution sources
- capability snapshot version and capabilities used
- attachment filename, MIME type, byte size, and digest
- token usage, latency, status, and structured error code

Prompt, response, raw file, and extracted file contents are excluded from operational logs by
default. Any diagnostic content logging must be separately controlled, redacted, and retention
bounded.

Emitting normal logs or metrics to the deployment's configured sinks is not an ORM/backend API
dependency. The endpoint must not write an operational request record to the Main Sequence backend.

## Only Allowed Backend Dependency: Provider Credentials

The only Main Sequence backend operation allowed while handling a sessionless response is provider
credential hydration or refresh. Environment-provided credentials may avoid even that call.

Credential hydration must support the locally authenticated user, bound `agent_uid`, and requested
provider without requiring `agent_session_uid`. The backend contract should either:

- allow `agent_session_uid: null` while requiring `agent_uid`, or
- expose a dedicated sessionless agent credential-hydration operation.

The credential operation may verify that the runtime and user may obtain credentials for the
requested agent/provider. It must not return agent defaults, capabilities, session state, or other
execution configuration as a side channel.

Credential refresh, when supported by a provider, must preserve the same authenticated user,
agent UID, provider, and request authorization.

During the request, Astro must not call backend operations for:

- agent or Agent Card retrieval
- capability listing or capability content
- session creation or retrieval
- entries, checkpoints, leases, or runtime state
- A2A task, message, output, or event persistence
- project or workspace metadata
- request/response audit persistence

If the local snapshot is missing or invalid, Astro fails locally. If credentials cannot be resolved,
Astro returns `provider_credentials_unavailable`. Neither failure may trigger a session creation or
a fallback backend configuration lookup.

## Errors

Errors use the ADR 37 A2A HTTP error envelope and field-violation detail shape. Sessionless-specific
codes are carried as structured error details rather than through a second endpoint-specific error
schema.

Stable detail codes include:

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

The JSON and terminal SSE paths must call the same shared A2A error serializer. FastAPI `detail`
strings and provider-native errors must not leak as an alternate public contract.

## Observability

Recommended events:

- `agent_response_request_started`
- `agent_response_local_snapshot_loaded`
- `agent_response_model_resolved`
- `agent_response_attachment_validated`
- `agent_response_capabilities_resolved`
- `agent_response_provider_first_output`
- `agent_response_request_completed`
- `agent_response_request_failed`
- `agent_response_cleanup_completed`

Timing should separate local authentication, local snapshot loading, credential resolution,
attachment processing, capability execution, provider first output, and provider completion.

Logs must make it explicit that no backend call other than credential hydration/refresh was made and
that no session or task was created.

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

- A2A `Message`, `Part`, file, output, error, and SSE behavior has one source of truth
- callers can use agent defaults and safe capabilities without manufacturing a session
- model and thinking overrides remain available per request
- one-shot document and image questions gain explicit capability and modality validation
- Chat, A2A, raw model inference, and one-shot agent inference have distinct persistence semantics

Costs:

- the current A2A parsing and materialization code must be separated from session/task side effects
- deployment must package and refresh a complete local agent execution snapshot
- the backend needs sessionless, agent-scoped credential support only
- capabilities need sessionless and mutation metadata
- provider adapters need real multimodal serialization before image support can be advertised
- request-scoped attachment extraction and cleanup require dedicated tests
- client applications need a separate "Ask once" flow

## Non-Goals

- Do not create an ephemeral or hidden backend session.
- Do not fetch agent configuration, capabilities, cards, projects, or authorization from the backend
  during request handling.
- Do not preserve conversation memory between response requests.
- Do not make all agent capabilities available outside a session.
- Do not permit silent fallback to another agent, provider, model, or media-processing strategy.
- Do not change the agent's persisted defaults when applying request overrides.
- Do not replace Chat, A2A, or ADR 36.

## Implementation Plan

### Phase 1: Shared Contract And Deployment Snapshot

- [ ] Extract canonical A2A `Message`, `Part`, configuration, metadata-extension, prepared-input,
      direct-message response, SSE, and error components from the durable A2A handler.
- [ ] Make durable A2A and sessionless agent responses consume those shared components.
- [ ] Add policy inputs for context requirements, task support, materialization root, and
      persistence instead of branching inside duplicate parsers.
- [ ] Define a versioned local agent execution snapshot schema keyed by `agent_uid`.
- [ ] Package or mount the resolved snapshot and all required prompt/capability content before
      Astro startup.
- [ ] Validate the requested `agent_uid` and caller claims locally.
- [ ] Add sessionless, agent-authorized provider credential hydration.
- [ ] Define capability metadata for sessionless eligibility, mutations, and media types.
- [ ] Add the declared
      `https://mainsequence.ai/a2a/extensions/agent-inference/v1` request/response model.

### Phase 2: Text-Only Agent Responses

- [ ] Implement `POST /api/agents/{agent_uid}/responses`.
- [ ] Require the A2A request content type and return the direct A2A `Message` response branch.
- [ ] Implement agent default and request override resolution.
- [ ] Compose the agent instructions and prompt capabilities from the immutable snapshot.
- [ ] Execute a text-only response without session, checkpoint, task, or transcript writes.
- [ ] Assert the non-persistence boundary in integration tests.
- [ ] Run the same message/part/output/error conformance fixtures against durable A2A and the
      sessionless endpoint.

### Phase 3: Safe Capabilities And Attachments

- [ ] Add request-scoped attachment normalization, limits, and cleanup.
- [ ] Add bounded text and embedded-PDF-text extraction.
- [ ] Add sessionless-safe read-only capabilities with multi-turn tool execution.
- [ ] Add provider/model modality metadata and native image serialization.
- [ ] Add DOCX and other formats only behind installed capability declarations.

### Phase 4: Streaming And Clients

- [ ] Implement `POST /api/agents/{agent_uid}/responses/stream`.
- [ ] Use the shared A2A SSE encoder and final direct `Message` event.
- [ ] Do not add endpoint-specific token, model, tool, or reasoning delta events.
- [ ] Cancel work and clean attachments on disconnect.
- [ ] Add client helpers and an "Ask once" UI with file validation.
- [ ] Document retry, timeout, and idempotency behavior.

### Phase 5: Verification

- [ ] Add endpoint tests for authentication, agent authorization, defaults, and every override
      combination.
- [ ] Add provider-credential integration tests with no session UID.
- [ ] Add tests that fail if the endpoint calls any backend operation other than provider
      credential hydration or refresh.
- [ ] Add tests proving requests still resolve agent defaults and capabilities when every
      non-credential backend operation is unavailable.
- [ ] Add attachment tests for supported, unsupported, malformed, oversized, encrypted, and
      cleanup cases.
- [ ] Add parity tests proving identical parts normalize and serialize identically on durable A2A
      and sessionless paths.
- [ ] Add tests proving unsupported parts are never ignored.
- [ ] Add tests proving no session, checkpoint, task, transcript, or raw file is persisted.
