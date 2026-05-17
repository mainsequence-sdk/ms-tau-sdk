# ADR: Retire Project Coder In Favor Of Project Executor

## Status

Accepted

## Context

Astro currently has two project implementation roles:

- `mainsequence-project-coder`
- `mainsequence-project-executor`

That split is no longer the desired architecture.

`mainsequence-project-coder` was built around a local checked-out project session. The orchestrator
selected or created a project, checked it out locally, then switched the active conversation into a
new project-scoped coder session. Astro also bootstrapped that coder runtime by running project SDK
checks, creating a local virtualenv, running `uv sync`, and then launching Pi inside the checkout.

`mainsequence-project-executor` is the newer runtime shape. It is a separate project execution
runtime, launched and routed through backend-owned session and runtime-access contracts. It runs in
a prepared project environment, either from an image-backed worker or a mounted local debug worker.

Keeping both roles creates a confused architecture:

- the orchestrator can still switch the user into a coder session
- executor exists, but some backend workflow metadata still aliases executor to coder
- specialist delegation still treats coder and executor as sibling local child specialists
- docs and prompts still teach implementation handoff through coder
- project bootstrap behavior still belongs to the old coder session shape

The target architecture is simpler:

- `astro-orchestrator` remains the user-facing control plane
- `mainsequence-project-executor` remains the only project implementation runtime
- `mainsequence-project-coder` is retired completely

## Problem

The current coder flow violates the direction established by backend-mediated executor runtimes.

The old flow is:

1. orchestrator prepares or checks out a project locally
2. orchestrator uses the old project-session handoff path
3. Astro creates a `mainsequence-project-coder` session
4. the active user conversation moves into that coder session
5. coder performs project implementation inside the local checkout

The immediate replacement flow should be:

1. orchestrator creates `project_blueprint.md` through the existing project-creation skill
2. orchestrator creates the new Main Sequence project
3. orchestrator sets the project up locally
4. orchestrator waits until `is_initialized=true`
5. orchestrator resolves the deterministic checked-out project path
6. orchestrator copies `project_blueprint.md` into the checked-out project root
7. orchestrator commits and pushes that change
8. the flow stops there for now

The important difference is that the orchestrator should not switch the user into another Pi session
as the implementation agent. The orchestrator remains the conversation owner, and the first
replacement milestone is blueprint persistence in the created project checkout rather than immediate
executor invocation.

When `mainsequence-project-executor` is used later, orchestrator-to-executor communication is
A2A-only. That communication does not transfer session ownership or move the active user
conversation into executor.

## Decision

Astro will retire `mainsequence-project-coder`.

`mainsequence-project-executor` remains and becomes the only project implementation agent.

The orchestrator will no longer use project-session handoff to move the active conversation into a
project-local coding agent. For the immediate migration scope, the orchestrator will use the
existing `project_blueprint.md` contract from project creation, copy that blueprint into the
initialized checked-out project, commit it, and push it. Executor invocation remains a follow-up
phase after that deterministic creation workflow is live.

## Non-Goals

- Do not remove `mainsequence-project-executor`.
- Do not make `astro-orchestrator` the main code implementer.
- Do not keep `mainsequence-project-coder` as a hidden compatibility specialist.
- Do not create a second handoff artifact for newly created projects.
- Do not use project-session handoff as a disguised project workflow.
- Do not implement executor invocation as part of this immediate project-creation workflow.
- Do not model executor use as a session handoff or ownership transfer.

## Target Architecture

### Roles

`astro-orchestrator`:

- owns the user-facing conversation
- selects or creates the project
- gathers enough project context to define the requested work
- creates `project_blueprint.md` through the project-creation skill
- sets the new project up locally
- waits for readiness
- copies `project_blueprint.md` into the deterministic checked-out project root
- commits and pushes that change
- summarizes creation/setup results back to the user
- if executor is used later, communicates with it only through A2A while remaining the owner of the
  user-facing session

`mainsequence-project-executor`:

- remains the future project implementation runtime
- is retained as part of the target architecture
- is not invoked by the immediate project-creation workflow defined in this ADR phase

Backend:

- owns executor discovery
- owns executor session allocation
- owns runtime access resolution
- owns runtime URL/token issuance
- routes or enables the A2A request to the target executor runtime

### Removed Role

`mainsequence-project-coder` is no longer a valid:

- frontend `agentType`
- backend `agent_type`
- project-session target
- old project-session handoff target
- direct specialist-launch target
- prompt-layer implementation role

## Project Blueprint Contract

For newly created projects, the orchestrator must reuse the existing `project_blueprint.md`
contract from the project-creation skill.

Rules:

- `project_blueprint.md` is created before project creation
- after `mainsequence project create` succeeds, Astro runs the finalize helper, which sets the
  project up locally and copies `project_blueprint.md` into the checked-out project root
- Astro does not invent a second handoff artifact for this flow
- existing-project selection does not automatically require or generate `project_blueprint.md`

## Commit And Push Contract

After copying `project_blueprint.md` into the initialized checked-out project root, the orchestrator
must:

1. `git add project_blueprint.md`
2. create a commit
3. push the change

Recommended commit message:

```text
chore(mainsequence): add project blueprint
```

## Deferred Executor Invocation

`mainsequence-project-executor` remains the intended implementation runtime, but executor
invocation is not part of this immediate workflow.

If executor is introduced in a later phase, the contract is:

- all orchestrator-to-executor communication is A2A
- A2A does not transfer session ownership
- the orchestrator remains the active user-facing session
- executor results are consumed as A2A output, not as a session switch

That means this ADR phase does not yet require:

- executor A2A request fields
- runtime-access polling
- a new handoff artifact format
- executor-visible commit metadata

## Current Code Dependencies To Remove

The existing code still has several `mainsequence-project-coder` dependencies that must be removed
or replaced.

### Stream Runtime

- `PROJECT_SESSION_AGENT_TYPES` currently includes coder and executor.
- `resolveBackendWorkflowKeyForAgent(...)` maps executor sessions back to the coder workflow key.
- `parseSessionSwitchRequest(...)` only accepts coder session switches.
- `handleProjectSessionSwitch(...)` creates and continues a coder session in the same stream.
- pending onboarding and pending runtime bootstrap are coder-only concepts.
- project runtime bootstrap is still named and modeled as coder bootstrap.
- coder-only diff/session endpoints still exist on the active API surface.

Target state:

- only `astro-orchestrator` and `mainsequence-project-executor` remain valid runtime agents
- executor workflow metadata uses executor identity directly
- session switch is removed from the project implementation flow
- coder bootstrap state is removed
- project diff/session tooling is removed from the active API surface until executor has a real replacement

### Tools

- the old project-session handoff path should be removed.
- the generic `delegate_specialist` tool should be removed from the active tool surface.
- the old direct `run_specialist` launcher should be removed from the active tool surface.
- Executor invocation is deferred until after the project-blueprint copy/commit/push workflow is
  live.

### Prompts

- `.pi/APPEND_SYSTEM.md` must stop instructing orchestrator to delegate implementation to coder.
- the project-creation workflow prompt must switch from coder session handoff to the deterministic
  `project_blueprint.md` copy/commit/push workflow.
- tutorial and verification prompts must stop delegating to coder.
- project-coder prompt file should be deleted once no runtime path can load it.

### Docs

Docs must stop presenting coder as a current agent or valid public contract:

- request contract
- response contract
- agent docs
- quickstart docs
- extension docs
- backend session allocation ADR
- A2A ADRs that still mention coder as an active collaborator

## Backend Contract Changes

The backend should no longer need a `mainsequence-project-coder` runtime routing key for new Astro project
work.

New executor sessions should use:

```text
agent_type = "mainsequence-project-executor"
```

The backend session serializer should remain session-first for model and runtime configuration.

Old coder sessions may remain readable for historical sessions, but Astro should not create new
coder sessions after this migration.

## Frontend Contract Changes

The frontend should stop expecting project implementation to move into a coder session through a
dedicated session-handoff chunk.

The frontend should instead model the current creation flow as:

- orchestrator session remains active
- project creation and local setup stay in orchestrator
- no dedicated session-handoff chunk appears for project implementation
- successful completion means the initialized checkout now contains committed `project_blueprint.md`

## Migration Plan

### Phase 1: Remove Coder As The Intended Product Flow

This phase changes the architecture first, before introducing any executor invocation flow.

- remove project-session handoff language from orchestrator prompts and user guidance
- stop presenting `mainsequence-project-coder` as a valid implementation path in docs
- stop presenting executor as a normal child specialist
- remove coder from specialist guidance and runtime discovery surfaces
- update frontend and response-contract language so project work no longer implies a session switch
- mark coder as deprecated for new live work even if the runtime path still exists temporarily

The goal of this phase is to make coder non-authoritative before the replacement flow is wired.

### Phase 2: Stop Creating New Coder Sessions

- stop creating backend sessions with `agent_type = "mainsequence-project-coder"`
- remove executor-to-coder runtime routing aliases
- remove the old project-session handoff path completely
- stop treating coder as a valid frontend `agentType` for new work
- keep old coder sessions readable as history only

The goal of this phase is to ensure no new live work enters coder, even before all old code is
deleted.

### Phase 3: Persist `project_blueprint.md` Into Created Projects

- copy `project_blueprint.md` into the initialized checked-out project root
- commit that copied blueprint
- push the commit
- make this the only supported completion step for the new-project creation workflow

### Phase 4: Defer Executor Invocation

- keep `mainsequence-project-executor` as the retained implementation runtime
- do not wire executor invocation into this immediate project-creation workflow yet
- revisit ADR 25 only after the blueprint copy/commit/push workflow is live

### Phase 5: Remove Coder Runtime Code

- delete coder prompt file
- remove coder from runtime agent allowlists
- remove coder bootstrap state and project runtime bootstrap paths
- remove coder from specialist delegation and direct specialist launch
- remove or redefine coder-only diff/session tools

### Phase 6: Clean Docs And Historical References

- update request and response contracts
- update component docs and quickstarts
- update ADRs that refer to coder as a current active role
- preserve historical ADR references only where they describe old behavior explicitly

## Compatibility

Existing historical `mainsequence-project-coder` sessions may still exist in backend and checkpoint
storage.

Migration policy:

- do not create new coder sessions
- allow old coder sessions to be displayed as historical records
- do not attempt to resume old coder sessions as live implementation sessions after the removal
- return a clear `agent_retired` style error if a client tries to start or resume coder for live
  work

## Risks

### Blueprint Commit Visibility

If the blueprint copy/commit step is skipped or pushed incorrectly, the created project will not
contain the durable creation instructions that this workflow is supposed to preserve.

Mitigation:

- resolve the deterministic checked-out path before copying
- commit the copied `project_blueprint.md`
- push immediately as part of the same workflow

### Lost Diff Tool Behavior

The old coder-only diff/session endpoints have been removed and executor does not yet expose a
replacement.

Mitigation:

- keep them removed until executor exposes a deliberate replacement

### Prompt Drift

If any prompt still says "switch into coder", the old behavior may reappear.

Mitigation:

- update prompts before removing code
- add tests or prompt checks for banned coder references in active runtime prompts

### Backend Runtime Routing Drift

Executor currently has places where workflow metadata maps back to coder.

Mitigation:

- remove executor-to-coder aliases before disabling coder
- assert new executor sessions use `agent_type = "mainsequence-project-executor"`

## Verification Plan

- confirm orchestrator can complete existing-project setup without any project-session handoff path
- confirm orchestrator creates `project_blueprint.md` before project creation
- confirm orchestrator waits for `is_initialized=true`
- confirm orchestrator copies `project_blueprint.md` into the initialized checked-out project root
- confirm the copied `project_blueprint.md` is committed
- confirm the copied `project_blueprint.md` is pushed
- confirm no new backend sessions are created with `agent_type = "mainsequence-project-coder"`
- confirm `mainsequence-project-coder` is not listed in available runtime agents
- confirm `delegate_specialist` is no longer part of the active tool surface
- confirm there is no active direct `run_specialist`-style launcher for project implementation
- confirm old coder sessions produce a clear retired-agent response if resumed for live work

## Completed Verification Evidence

The current repo state closes the ADR 26 migration at the code-and-contract level:

- [`../getting-started/request-lifecycle.md`](../getting-started/request-lifecycle.md)
  defines the current flow as orchestrator-owned project creation with no session switch
- active project-session handoff, `delegate_specialist`, and direct `run_specialist` launch
  surfaces were removed from the codebase

Later runtime-profile work removed the local checkout finalization helper; project implementation is
owned by backend-mediated `mainsequence-project-executor` sessions rather than orchestrator-local
checkout setup.
- coder-only diff/session endpoints were removed from the active API surface
- public request/response docs and backend session-allocation docs now describe the
  executor-first contract instead of the retired coder flow

This verifies the intended stop condition for the current phase:

- new project creation ends at committed and pushed `project_blueprint.md`
- the user-facing session remains `astro-orchestrator`
- executor invocation is explicitly deferred to follow-on ADRs

## Tasks

- [x] Remove project-session handoff from active orchestrator prompts.
- [x] Remove the old project-session handoff path completely.
- [x] Remove coder from public docs, prompts, and response-contract language as the intended
      implementation path.
- [x] Remove executor from normal child-specialist guidance and discovery surfaces.
- [x] Remove `delegate_specialist` from the active tool surface.
- [x] Stop mapping executor backend workflow keys to coder.
- [x] Stop creating new backend sessions with `agent_type = "mainsequence-project-coder"`.
- [x] Remove coder from `delegate_specialist`.
- [x] Remove the old direct `run_specialist` launcher surface.
- [x] Copy `project_blueprint.md` into the initialized checked-out project root for new project creation.
- [x] Add commit and push steps for the copied `project_blueprint.md`.
- [x] Defer executor invocation follow-up to ADR 25 after the blueprint copy/commit/push workflow is live.
- [x] Remove `mainsequence-project-coder` from runtime agent allowlists.
- [x] Remove coder-specific pending onboarding and runtime bootstrap state.
- [x] Remove or redefine coder-only diff/session tools.
- [x] Delete `.pi/agents/mainsequence-project-coder.md`.
- [x] Update public request and response docs.
- [x] Update backend session allocation docs.
- [x] Add verification that the new project-creation flow stops at committed and pushed `project_blueprint.md` with no session switch.
