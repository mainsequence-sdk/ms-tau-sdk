# Decisions

This file indexes current decisions only. Historical transition ADRs, superseded proposals, and
pre-UID API sketches have been removed from the active reference set.

## Runtime Identity

- [`adr-29-agent-type-identity.md`](./adr-29-agent-type-identity.md)
  - `agentType` / `agent_type` is the backend/runtime identity field.
- [`adr-30-runtime-profiles-vs-agent-type.md`](./adr-30-runtime-profiles-vs-agent-type.md)
  - Astro exposes only `astro-orchestrator` and `project-executor` as runtime/backend types.
- [`adr-31-backend-uid-identity.md`](./adr-31-backend-uid-identity.md)
  - Backend resource lookup is by `uid`, not deprecated `id`.
- [`../components/deployment-identities.md`](../components/deployment-identities.md)
  - Fixed executor deployment is selected by `ASTRO_FIXED_AGENT_TYPE=project-executor` plus
    project cwd; `ASTRO_EXECUTION_MODE=remote_project_worker` is topology metadata only.

## Session Attach And A2A

- [`adr-27-backend-only-session-initiation.md`](./adr-27-backend-only-session-initiation.md)
  - Astro attaches to existing backend sessions; chat and A2A do not create sessions.
- [`adr-25-production-a2a-discovery-and-runtime-access.md`](./adr-25-production-a2a-discovery-and-runtime-access.md)
  - Production A2A uses backend/CLI runtime access resolution.
- [`adr-28-durable-a2a-session-envelope.md`](./adr-28-durable-a2a-session-envelope.md)
  - A2A caller/linkage metadata is durable session state, not prompt-only scaffolding.
- [`adr-33-warm-a2a-session-runtime.md`](./adr-33-warm-a2a-session-runtime.md)
  - A2A chat should reuse cached preflight and warm session runners instead of paying a full
    cold-start cost on every same-session turn.
- [`adr-34-a2a-output-contracts.md`](./adr-34-a2a-output-contracts.md)
  - A2A output options must suppress reasoning and enforce strict JSON in Astro, not only through
    prompt guidance.
- [`adr-35-a2a-runtime-attachment-protocol.md`](./adr-35-a2a-runtime-attachment-protocol.md)
  - Superseded proposal. Public A2A now continues sessions through `POST /api/a2a/v1/message:send`
    with `message.contextId = AgentSession.uid` and idempotent `message.messageId` handling.

## Stateless LLM Passthrough

- [`adr-36-stateless-llm-passthrough.md`](./adr-36-stateless-llm-passthrough.md)
  - Simple LLM calls should use a separate stateless JSON endpoint that skips sessions, checkpoints,
    Pi runners, capabilities, project attachment, queues, and persistence.

## Checkpoints And Session State

- [`persistent-state.md`](./persistent-state.md)
  - Containers are disposable; durable continuity comes from backend checkpoints and
    backend-owned session identity.
- [`adr-compaction-checkpoint-retention.md`](./adr-compaction-checkpoint-retention.md)
  - Compaction is a backend checkpoint retention boundary.
- [`adr-checkpoint-reasoning-annotations.md`](./adr-checkpoint-reasoning-annotations.md)
  - Reasoning presence is checkpoint metadata without duplicating raw reasoning text.
- [`adr-editable-session-config.md`](./adr-editable-session-config.md)
  - Session config editability is advertised through session-insights metadata and patched through
    a narrow config endpoint.

## Auth And Model Control

- [`adr-runtime-credential-auth.md`](./adr-runtime-credential-auth.md)
  - Deployed runtimes authenticate to Main Sequence with runtime credentials.
- [`adr-interactive-provider-signin.md`](./adr-interactive-provider-signin.md)
  - Interactive model-provider signin is represented as explicit attempt state.
- [`../interface/model-provider-auth.md`](../interface/model-provider-auth.md)
  - Current model-provider credential, signin, signoff, and catalog behavior.
- [`../interface/available-models.md`](../interface/available-models.md)
  - Current available-model discovery behavior.
- [`../interface/session-model.md`](../interface/session-model.md)
  - Current session model binding behavior.

## Skills And Capabilities

- [`adr-workspace-analysis-from-orchestrator.md`](./adr-workspace-analysis-from-orchestrator.md)
  - Workspace analysis is a first-class orchestrator capability backed by an injected skill.
- [`adr-32-agent-session-capability-bindings.md`](./adr-32-agent-session-capability-bindings.md)
  - Agent capabilities define defaults; session capabilities define session-local skill overlays.

## Static Runtime Shape

- `.pi/APPEND_SYSTEM.md`
  - Shared Astro instruction contract for both deployment identities.
- [`../components/settings-and-system-prompt.md`](../components/settings-and-system-prompt.md)
  - Pi settings and prompt contract.
- [`../components/scripts-and-runtime.md`](../components/scripts-and-runtime.md)
  - TypeScript runtime, startup scripts, and sidecar processes.

## Observability

- [`adr-38-dual-sink-logging-contract.md`](./adr-38-dual-sink-logging-contract.md)
  - Astro emits one structured internal log event and renders it separately for machine JSON logs
    and compact human terminal logs.
