# ADR 29: Backend Agent Type And Runtime Agent Identity

Status: Accepted
Date: 2026-05-12

## Context

Astro has two identities that must stay separate:

- Backend classification is the backend `Agent.agent_type` value. Astro names this `backendAgentType`.
- Runtime routing is the Astro prompt/runtime selector, such as `astro-orchestrator` or `mainsequence-project-executor`. Astro names this `runtimeAgentName`.

Those runtime routing values are not backend Agent type values. They select local behavior, prompts,
runtime cwd rules, project handling, and A2A routing.

Previous code blurred the boundary by using broad "agent name" terminology for backend Agent
classification, request routing, session metadata, prompt lookup, and A2A caller identity. That
made it too easy to route a prompt using backend Agent classification or to require the backend to
keep serializing a removed Agent identity field.

## Decision

Astro uses explicit current fields only:

| Area | Current Field |
| --- | --- |
| Backend Agent classification | `agent_type` / `agentType` |
| Astro TypeScript backend classification | `backendAgentType` |
| Chat request runtime selector | `runtimeAgentName` |
| Session metadata runtime selector | `runtimeAgentName` / `runtime_agent_name` |
| Stream protocol runtime selector | `runtime_agent_name` |
| A2A caller runtime selector | `callerRuntimeAgentName` / `caller_runtime_agent_name` |
| Prompt config value in TypeScript | `promptAgentName` |

Astro must not infer runtime routing from backend Agent type.

Astro must not infer backend Agent type from runtime routing.

Astro must not carry fallback parsing for removed Agent identity fields. Payloads that still use the
removed fields must be migrated before they reach Astro.

## Removed Fields

The following fields are removed from Astro's Agent identity contract:

| Removed Field | Replacement |
| --- | --- |
| chat request `agentName` | `runtimeAgentName` |
| session metadata `agentName` | `runtimeAgentName` |
| session metadata `agent_name` | `runtime_agent_name` |
| backend Agent `name` | `agent_type` |
| backend Agent `agentName` | `agentType` |
| backend Agent `agent_name` | `agent_type` |
| A2A `callerAgentName` | `callerRuntimeAgentName` |
| A2A `caller_agent_name` | `caller_runtime_agent_name` |
| backend/session `workflow_key` | session metadata `runtime_agent_name` |
| TypeScript `AgentConfig.name` | `AgentConfig.promptAgentName` |

These removed fields should not be accepted as aliases in request parsing, session hydration,
conversation history loading, backend Agent parsing, or A2A envelope parsing.

## Prompt Frontmatter

Markdown prompt files keep their existing frontmatter key:

```markdown
---
name: mainsequence-project-executor
description: ...
---
```

This `name` key belongs to the prompt-file schema. It is not backend `Agent.name`.

Astro must load frontmatter `name` into TypeScript as `promptAgentName` and must use
`promptAgentName` for prompt discovery, deduplication, temp prompt filenames, and
`ASTRO_ACTIVE_SPECIALIST`.

Changing the Markdown frontmatter key is a separate prompt-file-format migration and is out of scope
for this ADR.

## Implementation Tasks

- [x] Add `backendAgentType` to session metadata and session insight responses.
- [x] Parse backend Agent classification only from `agent_type` / `agentType`.
- [x] Parse runtime routing only from `runtimeAgentName` / `runtime_agent_name`.
- [x] Parse chat requests only from `runtimeAgentName`.
- [x] Parse A2A caller identity only from `callerRuntimeAgentName` / `caller_runtime_agent_name`.
- [x] Remove request parsing support for `agentName`.
- [x] Remove session metadata read support for `agentName` and `agent_name`.
- [x] Remove backend Agent parsing support for `name`, `agentName`, and `agent_name`.
- [x] Remove A2A parsing support for `callerAgentName` and `caller_agent_name`.
- [x] Remove runtime routing support for `workflow_key`.
- [x] Rename `RequestContext.agentName` to `runtimeAgentName`.
- [x] Rename `ActiveStreamSession.agentName` to `runtimeAgentName`.
- [x] Rename `SessionMetadata.agentName` to `runtimeAgentName`.
- [x] Add `SessionMetadata.backendAgentType`.
- [x] Rename conversation history session identity to `runtimeAgentName`.
- [x] Rename session insights identity to `runtimeAgentName`.
- [x] Expose `backendAgentType` separately in session insights.
- [x] Rename A2A envelope caller identity to `callerRuntimeAgentName`.
- [x] Rename A2A user-message provenance caller identity to `callerRuntimeAgentName`.
- [x] Rename `resolveCurrentAstroAgentName(...)` to `resolveCurrentRuntimeAgentName(...)`.
- [x] Rename `buildA2ASystemInstruction(...)` input to `callerRuntimeAgentName`.
- [x] Rename specialist prompt config usage to `promptAgentName` in TypeScript.
- [x] Keep Markdown prompt frontmatter `name` only as prompt-file schema input.
- [x] Update stream protocol metadata to `runtime_agent_name`.
- [x] Update interface docs and ADR examples to use `runtimeAgentName`, `runtime_agent_name`, `backendAgentType`, and `agent_type`.
- [x] Audit the repo for removed identity fields in active code and current docs.
- [ ] Add focused tests for backend session payloads with `agent_type` and no removed identity fields.
- [ ] Add focused tests confirming prompt lookup uses `runtimeAgentName` / `promptAgentName`, never `backendAgentType`.
- [ ] Add focused tests for A2A payloads using `callerRuntimeAgentName` / `caller_runtime_agent_name`.
- [ ] Audit persisted runtime session files before deploying this change to an environment that may still contain old local state.

## Consequences

- Agent identity is stricter and easier to reason about.
- Clients must send `runtimeAgentName` on chat requests.
- Backend serializers must provide backend Agent classification as `agent_type`.
- Session serializers must include runtime routing in session metadata as `runtime_agent_name`.
- A2A callers must identify themselves with `callerRuntimeAgentName` or `caller_runtime_agent_name`.
- Old local session metadata/history files need one-time migration or removal before reuse.
- Prompt Markdown files continue using frontmatter `name`, but TypeScript must never expose that as `AgentConfig.name`.
