# ADR 29: Agent Type Identity

Status: Accepted
Date: 2026-05-12

## Context

Backend `Agent.name` is no longer part of the Agent contract. Astro must identify agents with
`Agent.agent_type`, and the values must match the local prompt/frontmatter identities such as
`astro-orchestrator` and `project-executor`.

Previous code blurred that contract by using broad name-based fields and by introducing a separate
runtime-name vocabulary. That made request routing, backend session hydration, stream metadata, and
A2A caller identity harder to reason about than necessary.

## Decision

Astro uses `agentType` as the only TypeScript/request identity field for backend Agent
communication and runtime routing. Serialized backend-style payloads use `agent_type`.

| Area | Current Field |
| --- | --- |
| Chat request identity | `agentType` |
| Backend Agent classification | `agent_type` / `agentType` |
| Backend session metadata | `agent_type` / `agentType` |
| Astro session metadata | `agentType` |
| Stream protocol session metadata | `agent_type` |
| Conversation history session metadata | `agentType` |
| Session insights identity | `agentType` |
| A2A caller identity | `callerAgentType` / `caller_agent_type` |
| Prompt config value in TypeScript | `promptName` |

Markdown prompt files keep their existing frontmatter key:

```markdown
---
name: project-executor
description: ...
---
```

That `name` key belongs only to the prompt-file schema. Astro loads it into TypeScript as
`promptName` for prompt discovery, deduplication, temp prompt filenames, and
`ASTRO_ACTIVE_SPECIALIST`. It must not be exposed as backend `Agent.name`.

## Removed Fields

The following fields are removed from Astro's active identity contract and must not be accepted as
aliases in request parsing, session hydration, conversation history loading, backend Agent parsing,
or A2A envelope parsing:

| Removed Field | Replacement |
| --- | --- |
| chat request `agentName` | `agentType` |
| chat request `runtimeName` | `agentType` |
| chat request `runtimeAgentName` | `agentType` |
| session metadata `agentName` | `agentType` |
| session metadata `agent_name` | `agent_type` |
| session metadata `runtimeName` | `agentType` |
| session metadata `runtime_name` | `agent_type` |
| backend Agent `name` | `agent_type` |
| backend Agent `agentName` | `agentType` |
| backend Agent `agent_name` | `agent_type` |
| A2A `callerAgentName` | `callerAgentType` |
| A2A `caller_agent_name` | `caller_agent_type` |
| A2A `callerRuntimeName` | `callerAgentType` |
| A2A `caller_runtime_name` | `caller_agent_type` |
| backend/session `workflow_key` | `agent_type` |
| TypeScript `AgentConfig.name` | `AgentConfig.promptName` |

## Implementation Tasks

- [x] Parse chat requests only from `body.agentType`.
- [x] Parse backend Agent classification only from `agent_type` / `agentType`.
- [x] Parse session metadata only from `agent_type` / `agentType`.
- [x] Parse A2A caller identity only from `callerAgentType` / `caller_agent_type`.
- [x] Remove request parsing support for `agentName`, `runtimeName`, and `runtimeAgentName`.
- [x] Remove session metadata read support for `agentName`, `agent_name`, `runtimeName`, and `runtime_name`.
- [x] Remove backend Agent parsing support for `name`, `agentName`, and `agent_name`.
- [x] Remove A2A parsing support for `callerAgentName`, `caller_agent_name`, `callerRuntimeName`, and `caller_runtime_name`.
- [x] Remove runtime routing support for `workflow_key`.
- [x] Rename `RequestContext.agentName` / `RequestContext.runtimeName` to `RequestContext.agentType`.
- [x] Rename `ActiveStreamSession.agentName` / `ActiveStreamSession.runtimeName` to `ActiveStreamSession.agentType`.
- [x] Rename `SessionMetadata.agentName` / `SessionMetadata.runtimeName` to `SessionMetadata.agentType`.
- [x] Rename conversation history session identity to `agentType`.
- [x] Rename session insights identity to `agentType`.
- [x] Rename stream protocol metadata to `new_session.agent_type`.
- [x] Rename A2A envelope caller identity to `callerAgentType`.
- [x] Rename A2A user-message provenance caller identity to `callerAgentType`.
- [x] Rename `resolveCurrentAstroAgentName(...)` / `resolveCurrentRuntimeName(...)` to `resolveCurrentAgentType(...)`.
- [x] Rename `buildA2ASystemInstruction(...)` input to `callerAgentType`.
- [x] Rename specialist prompt config usage from `AgentConfig.name` to `AgentConfig.promptName`.
- [x] Keep Markdown prompt frontmatter `name` only as prompt-file schema input.
- [x] Rename fixed runtime deployment env from `ASTRO_FIXED_AGENT_NAME` / `ASTRO_FIXED_RUNTIME_NAME` to `ASTRO_FIXED_AGENT_TYPE`.
- [x] Rename fixed runtime server constant to `ASTRO_FIXED_AGENT_TYPE_ENV`.
- [x] Rename runtime allow-list constants to `PROJECT_SESSION_AGENT_TYPES` and `ALLOWED_AGENT_TYPES`.
- [x] Rename backend session helper output to `agentType`.
- [x] Remove the deprecated semantic Agent identity builder from the backend identity contract.
- [x] Update runtime tool output from `runtime_name` to `agent_type`.
- [x] Update runtime log labels from removed name/runtime-name terminology to `agent_type`.
- [x] Update interface docs and ADR examples to use `agentType`, `agent_type`, `callerAgentType`, and `caller_agent_type`.
- [x] Audit active code for removed identity fields.
- [x] Run TypeScript validation after the rename.
- [ ] Add focused tests for backend session payloads with `agent_type` and no removed identity fields.
- [ ] Add focused tests confirming prompt lookup uses `agentType` / `promptName`.
- [ ] Add focused tests for A2A payloads using `callerAgentType` / `caller_agent_type`.
- [ ] Audit persisted runtime session files before deploying this change to an environment that may still contain old local state.

## Consequences

- Clients must send `agentType` on chat requests.
- Backend serializers must provide Agent classification as `agent_type`.
- Session serializers must include session identity as `agent_type`.
- A2A callers must identify themselves with `callerAgentType` or `caller_agent_type`.
- Old local session metadata/history files that only contain removed fields need one-time migration or removal before reuse.
- Prompt Markdown files continue using frontmatter `name`, but TypeScript must expose that value only as `promptName`.
