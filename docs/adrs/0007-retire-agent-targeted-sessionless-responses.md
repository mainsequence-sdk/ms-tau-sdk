# ADR 0007: Retire Agent-Targeted Sessionless Responses

Status: Accepted

Date: 2026-09-19

## Context

The former Astro deployment exposed `POST /api/agents/{agent_uid}/responses` and
`/responses/stream` for one independent model response without an AgentSession or A2A Task.
Historical ADR 44 supplied its defaults, instructions, media policy, and Agent identity through
an immutable deployment snapshot. ADR 56 retained that feature when Astro became the
workspace-installed `ms-tau-sdk` library.

That design now contradicts the library's single Tau configuration model. A normal coding or
A2A request runs a workspace-bound Tau `CodingSession` with project `.tau` resources and tools;
the agent-targeted response creates a separate, tool-free `AgentHarness` whose instructions come
from `MAINSEQUENCE_TAU_AGENT_EXECUTION_SNAPSHOT`. The response stream buffers execution and emits
only a final event. The snapshot is not a durable Tau session snapshot, and the checked deployment
source does not produce it. The one-shot endpoints are a second model-inference product surface,
not Tau agent execution.

## Decision

1. Remove both agent-targeted response routes and the one-shot harness, snapshot model,
   environment settings, request-scoped attachment storage, and one-shot-only dependencies.
   Do not add an alias, stub, or replacement TAU endpoint.
2. Stop advertising response paths or Agent-only runtime access in Main Sequence's backend and
   Python SDK. Remove `Agent.respond()` and `Agent.stream_response()` and the Agent-only
   credential-hydration identity branch used by this feature. Keep session- and authenticated-
   user-scoped hydration.
3. Preserve normal chat, deployed and local A2A Message/Task execution, durable Tau session
   snapshots, project `.tau` behavior, Main Sequence MCP, and provider credential handling.
   A direct A2A Message is still agent execution and is not the retired one-shot model endpoint.
4. Raw or client-agnostic model inference belongs to the platform's model-provider/inference
   domain, not this SDK. This decision does not claim that a provider catalog endpoint executes
   inference or require a new inference endpoint to be built as part of removal.
5. Make a coordinated breaking release across the TAU SDK, backend, and Main Sequence Python
   SDK. No compatibility period or dual-mode runtime is introduced.

## Prior Decisions

This ADR supersedes the re-adoption of historical Astro ADR 44 in ADR 56's disposition ledger.
It amends ADR 0002's runtime/HTTP surface and ADR 56's list of retained SDK primitives. The
historical ADR remains archived as a record, not an active contract.

## Verification

- The TAU SDK's OpenAPI and route table do not contain `/api/agents/{agent_uid}/responses` or
  `/responses/stream`, and neither path is executable.
- No active deployment injects `MAINSEQUENCE_TAU_AGENT_EXECUTION_SNAPSHOT` or its former
  `ASTRO_AGENT_EXECUTION_SNAPSHOT` name; one-shot asset settings and direct dependencies are gone.
- The backend no longer advertises Agent response paths or exposes Agent-only runtime access;
  AgentSession access and local-development credential hydration remain intact.
- The Main Sequence Python SDK has no `Agent.respond()` or `Agent.stream_response()` methods.
- Durable and local chat/A2A, provider, MCP, and session snapshot tests continue to pass.
