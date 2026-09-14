# Decisions

This file indexes current decisions only. Historical transition ADRs, superseded proposals, and
pre-UID API sketches have been removed from the active reference set.

## Runtime Identity

- [`adr-29-agent-type-identity.md`](./adr-29-agent-type-identity.md)
  - `agentType` / `agent_type` is the backend/runtime identity field.
- [`adr-30-runtime-profiles-vs-agent-type.md`](./adr-30-runtime-profiles-vs-agent-type.md)
  - Superseded for local runtime architecture by ADR 39; backend identity values remain valid.
- [`adr-39-unified-pi-runtime-context.md`](./adr-39-unified-pi-runtime-context.md)
  - Astro Core should use one Pi runtime context; orchestrator is the repository-independent edge case, and
    backend `agent_type` must not define separate local runtime architectures.
- [`adr-31-backend-uid-identity.md`](./adr-31-backend-uid-identity.md)
  - Backend resource lookup is by `uid`, not deprecated `id`.
- [`adr-41-remove-agent-unique-id.md`](./adr-41-remove-agent-unique-id.md)
  - `agent_unique_id` is removed from runtime behavior, persistence, public responses, adapter
    contracts, and docs. Astro stream responses must stop emitting Agent identity headers.
- [`../components/deployment-identities.md`](../components/deployment-identities.md)
  - CodeRepository attachment is selected by fixed code repository cwd; backend identity is separate metadata.

## Session Attach And A2A

- [`adr-27-backend-only-session-initiation.md`](./adr-27-backend-only-session-initiation.md)
  - Astro attaches to existing backend sessions; chat and A2A do not create sessions.
- [`adr-25-production-a2a-discovery-and-runtime-access.md`](./adr-25-production-a2a-discovery-and-runtime-access.md)
  - Production Tau A2A uses Django MCP for discovery/session/access and Astro's constrained host
    tool for the direct runtime message.
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
    with `message.contextId = AgentSession.uid`; ADR 47 limits durable send idempotency to the Task
    path and forbids creating hidden Tasks for direct Message sends.
- [`adr-42-a2a-file-parts-and-pdf-intake.md`](./adr-42-a2a-file-parts-and-pdf-intake.md)
  - Public A2A should accept standard file parts for PDF input through `Part.raw` or `Part.url`,
    materialize them into session assets, and pass a safe file manifest to Pi.
- [`adr-43-backend-backed-a2a-task-persistence.md`](./adr-43-backend-backed-a2a-task-persistence.md)
  - Gap analysis for replacing Astro's process-local A2A task maps with backend-backed
    `AgentTask`, `AgentTaskMessage`, `AgentTaskOutput`, and `AgentTaskEvent` persistence.
- [`adr-46-disable-a2a-push-notifications.md`](./adr-46-disable-a2a-push-notifications.md)
  - Astro advertises `pushNotifications: false` and returns the standard explicit unsupported
    response until canonical backend persistence and webhook delivery exist.

## Sessionless Inference

- [`adr-36-stateless-llm-passthrough.md`](./adr-36-stateless-llm-passthrough.md)
  - Superseded by ADR 44. The unscoped route is removed from the runtime and OpenAPI.
- [`adr-44-agent-targeted-sessionless-responses.md`](./adr-44-agent-targeted-sessionless-responses.md)
  - One-shot agent responses require `agent_uid`, resolve optional provider/model/thinking overrides
    over agent defaults, use only sessionless-safe capabilities, and create no session, task,
    transcript, checkpoint, or durable attachment.

## Checkpoints And Session State

- [`adr-45-nonblocking-tau-runtime-io.md`](./adr-45-nonblocking-tau-runtime-io.md)
  - Lease-owned Tau runtimes use cached reads and ordered write-behind persistence, flush before
    settlement and lease release, and reuse warm provider credentials until expiration.
- [`persistent-state.md`](./persistent-state.md)
  - Containers are disposable; durable continuity comes from backend checkpoints and
    backend-owned session identity.
- [`adr-compaction-checkpoint-retention.md`](./adr-compaction-checkpoint-retention.md)
  - Compaction is a backend checkpoint retention boundary.
- [`adr-checkpoint-reasoning-annotations.md`](./adr-checkpoint-reasoning-annotations.md)
  - Reasoning presence is checkpoint metadata without duplicating raw reasoning text.
- [`adr-editable-session-config.md`](./adr-editable-session-config.md)
  - Historical session-config editability contract; ADR 51 removes Astro's write endpoint and
    makes Django's AgentSession service the canonical mutation owner.

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
- [`../adrs/adr-52-enable-repository-tau-extensions-in-code-executors.md`](../adrs/adr-52-enable-repository-tau-extensions-in-code-executors.md)
  - CodeRepository Executor deployments enable Tau-native repository extensions as first-class
    tools in durable Chat and A2A sessions.

## Static Runtime Shape

- `.pi/APPEND_SYSTEM.md`
  - Shared Astro instruction contract for both deployment identities.
- [`../components/settings-and-system-prompt.md`](../components/settings-and-system-prompt.md)
  - Pi settings and prompt contract.
- [`../components/runtime-entrypoints-and-tools.md`](../components/runtime-entrypoints-and-tools.md)
  - TypeScript runtime, entrypoints, tools, and sidecar processes.

## Observability

- [`adr-38-dual-sink-logging-contract.md`](./adr-38-dual-sink-logging-contract.md)
  - Astro emits one structured internal log event and renders it separately for machine JSON logs
    and compact human terminal logs.

## Runtime Package Boundary

- [`adr-40-general-pi-deployment-runtime-package-boundary.md`](./adr-40-general-pi-deployment-runtime-package-boundary.md)
  - Astro Core should become a general Pi deployment runtime; Main Sequence behavior should move
    toward Pi package and backend adapter boundaries.
