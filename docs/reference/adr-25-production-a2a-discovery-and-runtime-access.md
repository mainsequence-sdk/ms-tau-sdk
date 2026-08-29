# ADR: Production A2A Discovery And Runtime Access

## Status

Accepted

## Note

The runtime-access and discovery portions of this ADR remain valid.

The session-creation portions are superseded by
[`adr-27-backend-only-session-initiation.md`](./adr-27-backend-only-session-initiation.md): the
backend must allocate the A2A target session before Astro is called, and Astro must not create that
session from the stream runtime.

The retry/reuse semantics for that backend allocation step are owned by the backend control plane
through backend ADR-007 (`A2A Target Session Allocation Idempotency`).

Astro is only a consumer of that contract:

- the backend allocation response is authoritative
- Astro should persist and reuse the returned `handle_unique_id`
- Astro should not restate or reimplement backend allocation internals in this repo

The Astro-side durable representation of the A2A caller/response/linkage envelope is covered by
[`adr-28-durable-a2a-session-envelope.md`](./adr-28-durable-a2a-session-envelope.md).

## Context

Astro already has:

- a prompt-layer A2A collaboration rule
- a local debug A2A shim using `A2A_DEV_PROJECT`
- an executor-facing streamer surface:
  - the standard A2A `message:send` route
  - `POST /api/a2a/cancel`

Astro previously lacked a real non-debug production discovery and communication flow.

That leaves a hole in the architecture:

- the orchestrator can decide that another agent should help
- but it does not have a real production path to find that agent, obtain runtime access, and send
  the A2A request

We now have an explicit backend-backed contract for that path through the Main Sequence CLI.

## Problem

This ADR closed the five concrete non-debug A2A gaps:

1. How does it discover candidate agents?
2. How does it choose the target agent?
3. How does it obtain the target communication session identity?
4. How does it obtain the runtime URL and bearer token for the selected target?
5. How does it send the actual A2A request to the target runtime?

The local debug shim is not enough because:

- it mocks discovery from `.agents/agent_card.json`
- it uses a fixed executor URL list
- it bypasses the real backend lifecycle and runtime access control plane

For non-debug A2A, the production path must be backend-mediated and must not invent pod URLs or
container URLs locally.

## Decision

Non-debug A2A discovery and communication will use the Main Sequence CLI as the backend-facing
control-plane client.

The end-to-end flow is:

1. semantic agent search
2. target selection
3. backend session allocation outside the Astro stream runtime
4. runtime access resolution
5. runtime health polling
6. streamed A2A request to the resolved runtime URL

The orchestrator does **not** derive the target URL from discovery.

The URL and bearer token come only from runtime access resolution.

## Discovery

### Command

Astro should perform non-debug A2A discovery with:

```bash
mainsequence agent search "<intent>" --limit 10 --json
```

The search prompt is the discovery prompt already constructed by the A2A tooling from:

- the bounded request
- any response-format requirement
- any agent hint

### Search result shape

The CLI returns search results like:

```json
[
  {
    "orm_class": "AgentSemanticSearchResult",
    "uid": "agent-25",
    "name": "code-repository-executor",
    "description": "A testing-only project that exposes two CLI-backed capabilities: return the current time and return the authenticated user.",
    "semantic_score": 0.7781205009695902,
    "text_score": 0.04040404,
    "combined_score": 0.5199197397584775
  }
]
```

### Candidate mapping

To preserve Astro's existing A2A discovery shape, each CLI result should be normalized to:

```json
{
  "agent_uid": "agent-25",
  "agent_description": "A testing-only project that exposes two CLI-backed capabilities: return the current time and return the authenticated user.",
  "a2a_card": {
    "name": "code-repository-executor",
    "semantic_score": 0.7781205009695902,
    "text_score": 0.04040404,
    "combined_score": 0.5199197397584775
  }
}
```

This keeps the public Astro discovery contract stable while making the backend search result the
source of truth.

### Selection rule

For non-debug A2A, Astro should treat the CLI search result ordering and scores as authoritative.

The default selection rule is:

1. choose the result with the highest `combined_score`
2. preserve backend/CLI result ordering as the tie-breaker

Astro should not apply a second local token-overlap ranking step in production mode.

## Session Allocation

After selecting the target agent, the backend control plane must allocate or provide the backend
session identity for that A2A communication before Astro is called.

Astro must receive that existing session id and treat it as the backend-owned A2A session key for
the remainder of the flow. Astro must not create the session from the stream runtime.

Per backend ADR-007, the allocation response is canonical and should include:

- `handle_unique_id`
- `agent_session_uid`
- `allocation_state`
- `session`

Astro should treat that backend response as authoritative.

Allocation/retry semantics are:

- first allocation may omit `handle_unique_id`; backend may generate one
- later retries and reconnects should reuse the returned `handle_unique_id`
- Astro should not invent its own correlation or restart identity locally

Because the sender already holds the backend session-allocation response at this point, it must
also forward the full backend `AgentSession` JSON serialization for that same target session in the
outbound Astro request under `session`.

This is a sender-side correctness rule, not just an optimization:

- do not send only `runtime_session_uid` plus messages
- do not trim the session payload down to a hand-picked subset
- do not rely on Astro's backend fetch fallback to recover model/provider/runtime metadata when the
  backend session JSON is already available to the sender
- Astro should durably materialize the resulting A2A envelope for that session instead of treating
  it as prompt-only request scaffolding

## Runtime Access Resolution

After backend session allocation, Astro should resolve runtime access with:

```bash
mainsequence agent session resolve_runtime_access <session_uid> --json
```

Example response:

```json
{
  "orm_class": "AgentSessionRuntimeAccess",
  "coding_agent_service_id": "42",
  "coding_agent_id": "code-repository-executor-service-81",
  "mode": "token",
  "rpc_url": "https://code-repository-executor-service-81.coding-agent-development.main-sequence.app/",
  "token": "..."
}
```

### Required fields

Astro must read:

- `rpc_url`
- `token`
- `mode`

Initial production support is limited to:

- `mode == "token"`

If `mode` is not `token`, Astro should fail the A2A request with a structured runtime-access error.

## Health Readiness

Before sending the A2A request, Astro should poll the resolved runtime health endpoint every 30
seconds using the resolved bearer token.

The health probe is:

```text
GET <rpc_url>/health
Authorization: Bearer <token>
```

Rules:

- treat the resolved `rpc_url` as the runtime base URL
- poll until the runtime reports healthy
- if the runtime never becomes healthy within the configured timeout budget, fail the A2A request
  as a runtime-readiness error

Astro must not guess an alternate runtime URL if `rpc_url` is unavailable or unhealthy.

## Runtime Request

After the runtime becomes healthy, Astro should send the actual A2A request to the resolved
runtime's existing A2A endpoint:

```text
POST <runtime-chat-url>
Authorization: Bearer <token>
Accept: text/event-stream
Content-Type: application/json
```

This intentionally uses the Astro A2A receiver surface, not the human-facing `POST /api/chat`
endpoint.

### Canonical runtime request body

The canonical non-debug A2A runtime payload is:

```json
{
  "runtime_session_uid": "session_123_uid",
  "session": {
    "uid": "session_123_uid",
    "thread_id": "123",
    "llm_provider": "openai-codex",
    "llm_model": "gpt-5.3-codex-spark",
    "session_metadata": {
      "agent_type": "code-repository-executor"
    }
  },
  "messages": [
    {
      "role": "user",
      "content": "hello"
    }
  ],
  "response_format": "Return a concise machine-facing status summary.",
  "caller": {
    "agent_id": 12,
    "agent_type": "astro-orchestrator"
  }
}
```

The example above is abbreviated for readability. The real sender should forward the full backend
session JSON serialization unchanged under `session`, not a trimmed subset.

At minimum, the payload must carry:

- the already-allocated target `runtime_session_uid`
- the full backend session serializer for that same session under `session`
- the machine-facing `messages`
- optional `response_format`
- caller metadata

### Streamer normalization requirement

Astro's legacy A2A chat normalizer must support this canonical A2A request shape in non-debug mode.

That means the streamer must accept:

- `runtime_session_uid`
- `runtimeSessionUid`
- `messages`

in addition to the current local-debug compatibility aliases like:

- `task`
- `message`
- `input`
- `prompt`
- `request`

The production path should use canonical `messages`, not the old task-alias shim.
The production path should also forward the full backend session serializer in `session` on every
non-debug A2A send.

## Cancellation

If Astro needs to cancel an in-flight non-debug A2A request, it should call the resolved runtime's
existing cancel endpoint:

```text
POST <rpc_url>/api/a2a/cancel
Authorization: Bearer <token>
Content-Type: application/json
```

with the backend-created target `runtime_session_uid`.

## Local Debug Relationship

`A2A_DEV_PROJECT` remains the local debug override.

When `A2A_DEV_PROJECT` is set:

- discovery may stay mocked from `.agents/agent_card.json`
- communication may keep routing directly to the local dev executor runtime

When `A2A_DEV_PROJECT` is **not** set:

- Astro must use the CLI-backed discovery and runtime-access flow defined by this ADR
- Astro must not fall back to guessed service URLs such as:
  - `http://<guessed-runtime-service>:8787`
  - `http://127.0.0.1:<port>`

Those direct URLs are debug-only behavior.

## Consequences

### Positive

- production A2A discovery becomes real instead of `not implemented`
- Astro no longer needs to guess or hardcode target runtime URLs in non-debug mode
- backend session allocation remains authoritative
- runtime access tokens remain backend-controlled
- the standard A2A `message:send` surface stays in use

### Negative

- Astro now depends on Main Sequence CLI execution for non-debug A2A
- non-debug A2A becomes a multi-step flow with several failure points:
  - search
  - session allocation
  - runtime access resolution
  - health readiness
- standard A2A `message:send` request
- The A2A path must support standard `Message` input through `POST /api/a2a/v1/message:send`.

## Verification Plan

- confirm non-debug discovery shells out to `mainsequence agent search ... --json`
- confirm Astro normalizes CLI search results into:
  - `agent_id`
  - `agent_description`
  - `a2a_card`
- confirm selection uses backend/CLI ranking rather than local token-overlap scoring
- confirm Astro requires an already-allocated backend session id before sending the A2A
  `message:send` request
- confirm the non-debug A2A sender forwards the full backend session serializer together with the
  target session id on every request
- confirm Astro resolves runtime access with
  `mainsequence agent session resolve_runtime_access <session_uid> --json`
- confirm Astro reads `rpc_url` and `token` from runtime access
- confirm Astro polls `GET <rpc_url>/health` with bearer auth every 30 seconds until healthy
- confirm Astro sends the A2A request to the runtime A2A chat transport, not `POST /api/chat`
- confirm Astro can cancel the run through `POST <rpc_url>/api/a2a/cancel`
- confirm non-debug mode no longer returns `a2a_backend_discovery_not_implemented`
- confirm non-debug mode no longer returns `a2a_backend_not_implemented`

## Tasks

- [x] Add a production A2A discovery adapter that shells out to
  `mainsequence agent search "<intent>" --limit 10 --json`.
- [x] Normalize CLI search results into Astro's A2A candidate contract.
- [x] Replace production-mode `not implemented` in the A2A discovery path.
- [x] Add production candidate selection using backend/CLI ranking.
- [x] Treat backend session allocation as an external control-plane step and require the resulting
  session uid on the Astro A2A request.
- [x] Add runtime-access resolution using
  `mainsequence agent session resolve_runtime_access <session_uid> --json`.
- [x] Add token-authenticated health polling against `GET <rpc_url>/health`.
- [x] Add a production A2A sender that targets the runtime A2A chat transport.
- [x] Add token-authenticated cancellation against `POST <rpc_url>/api/a2a/cancel`.
- [x] Extend legacy A2A chat normalization to accept canonical `runtime_session_uid` and
      `messages`.
- [x] Keep `A2A_DEV_PROJECT` as a debug-only override.
