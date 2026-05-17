# ADR 30: Keep Backend `agent_type`, Introduce Astro Runtime Profiles

Status: Accepted
Date: 2026-05-16
Implementation Status: Complete for the Astro runtime repository

## Context

Astro currently uses backend-visible `agent_type` values such as:

- `astro-orchestrator`
- `mainsequence-project-executor`

That distinction is important at the backend boundary. Backend session allocation, runtime access,
registration identity, analytics, and session ownership all need a stable `agent_type`.

At the same time, Astro has been using those same values too broadly inside the runtime. In
practice, the stream server has started to encode one architectural split in several overlapping
ways:

- backend/session identity through `agent_type`
- request/runtime identity through `agentType`
- deployment selection through fixed runtime `agent_type`
- worker topology through `ASTRO_EXECUTION_MODE`
- prompt and routing behavior through generic specialist discovery

That overlap makes the implementation heavier than necessary and has already created regressions.
Examples include:

- treating `mainsequence-project-executor` as a normal local specialist instead of a fixed worker
  runtime
- letting executor prompt loading fall through generic specialist discovery
- scattering executor-only cwd, prompt, model, and image assumptions across request handling
- mixing backend identity concerns with local runtime-behavior concerns

ADR 23 established that `mainsequence-project-executor` is a backend-mediated runtime rather than a
normal orchestrator specialist. ADR 29 established that backend and request identity should use
`agent_type` / `agentType`. This ADR refines the implementation boundary inside Astro itself.

## Problem

Astro is currently over-modeling executor behavior as if it were a second peer local agent rather
than a different runtime profile of the same stream runtime.

That causes several problems:

1. The backend identity distinction leaks into too many local runtime decisions.
2. Executor behavior is easy to accidentally route through generic orchestrator/specialist code
   paths.
3. Fixed worker assumptions such as project cwd, prompt strategy, and runtime wiring are not owned
   by one explicit concept.
4. Sidecar and runtime contracts are easier to misconfigure because “executor” is represented both
   as an agent identity and as a deployment mode.

The result is unnecessary complexity in request validation, prompt loading, session hydration,
model policy, A2A execution, and runtime deployment wiring.

## Decision

Astro will keep backend `agent_type` as the authoritative backend/session identity and deployment
selector, but Astro internals will introduce an explicit **runtime profile** concept for local
behavior only.

### Backend identity remains distinct

The backend-facing identities remain:

- `astro-orchestrator`
- `mainsequence-project-executor`

These values continue to be used for:

- backend `Agent.agent_type`
- session serializers
- agent registration identity
- backend session allocation and hydration
- A2A target session identity
- deployment/runtime selection
- frontend/backend request contracts

This ADR does **not** collapse those backend-visible identities.

### Astro runtime behavior moves to profiles

Inside Astro, behavior should primarily be selected by runtime profile, not by repeatedly branching
on `agentType`.

The initial profiles are:

- `orchestrator`
- `project_worker`

The runtime profile is derived primarily from the deployed or fixed `agent_type`.

`ASTRO_EXECUTION_MODE` may still describe worker topology, but it must not become a second parallel
identity vocabulary.

The runtime profile is therefore resolved from runtime configuration such as:

- `ASTRO_FIXED_AGENT_TYPE`
- `ASTRO_EXECUTION_MODE`
- `ASTRO_FIXED_PROJECT_CWD`

### Fixed worker rule

When Astro is running in fixed worker mode:

- the runtime profile is `project_worker`
- the fixed worker contract is the primary selector for local runtime behavior
- request `agentType` is still validated against the fixed worker identity, but it is not the main
  local prompt/routing selector

In other words:

- backend/session identity still says **what** session this is
- runtime profile says **how** this process should behave

### Session ownership is not a runtime-profile concern

Session ownership must not be treated as a separate runtime-profile boundary.

Ownership is already determined by the backend session attached to the request:

- `astro-orchestrator` sessions are orchestrator-owned sessions
- `mainsequence-project-executor` sessions are executor-owned sessions

The fact that an executor session may be delegated from an orchestrator flow does not create a new
Astro-local ownership abstraction. It is simply a backend/session relationship expressed through
distinct `agent_type` and `AgentSession` records.

### Executor is not a generic local specialist

`mainsequence-project-executor` must not be treated as a normal local specialist peer of
`astro-orchestrator`.

For executor runtime behavior:

- the base prompt comes from the executor runtime profile contract
- project-local prompt overrides may still exist when explicitly supported
- generic specialist discovery must not be the default base-prompt mechanism

### Canonical deployment contract

Astro should use one canonical runtime filesystem and sidecar contract across both deployment
profiles wherever possible.

That shared contract should cover:

- `HOME`
- `ASTRO_CONTAINER_DATA_DIR`
- `ASTRO_MAINSEQUENCE_CONFIG_DIR`
- `PI_CODING_AGENT_DIR`
- shared `/session-state` layout
- sidecar/main-container environment parity

The important difference between orchestrator and executor should be the effective working root, not
an entirely different Astro home-directory contract.

In practice:

- orchestrator uses its writable orchestrator runtime cwd
- executor uses its fixed prepared project cwd

Everything else should converge on the same Astro runtime contract unless a concrete external image
constraint makes divergence unavoidable.

### Prompt architecture should be unified

Astro should not maintain two large, mostly duplicated instruction sets just because backend
`agent_type` differs.

Astro should also avoid introducing hard capability restrictions purely from runtime role, such as:

- “executor must never do platform/control-plane work”
- “orchestrator must never do project implementation”

Those restrictions are too rigid for the intended product model and create artificial prompt
fragmentation.

The target prompt structure is:

1. one shared Main Sequence Astro instruction set
2. one small context-sensitive rule for whether the session is already attached to a prepared
   project cwd

The shared instruction set should own common behavior such as:

- Main Sequence auth/runtime-login behavior
- CLI failure contract
- A2A request contract
- shared response/reporting rules
- platform interaction
- project interaction
- workspace analysis
- implementation guidance

The main contextual switch is whether the runtime is already attached to a prepared project:

- if project-attached:
  - treat the current cwd as the prepared project root
  - do not create/select/set up a different project
  - prefer project-local instructions, status files, and task files when present
  - perform work in-place inside that project runtime
- if not project-attached:
  - allow project selection, creation, and setup flows
  - allow broader platform/control-plane behavior

This means backend/runtime identity and prompt capability should not be tightly coupled. The
important local prompt distinction is project attachment, not a separate role-specific capability
overlay.

### Profile-owned behavior

The following runtime behavior belongs to runtime profile, not to scattered `agentType` checks:

- prompt loading strategy
- cwd and project-root assumptions
- fixed project image/runtime assumptions
- worker-side model policy defaults
- sidecar/main-container environment parity rules
- project-worker-specific request validation

## Non-Goals

This ADR does not:

- remove `mainsequence-project-executor` as a backend `agent_type`
- make executor a child-specialist of orchestrator
- remove backend session ownership distinctions between orchestrator and executor sessions
- change the frontend/backend request contract away from `agentType`
- require backend `agent_type` values to imply different prompt capabilities
- require a single physical prompt file as the first implementation step; the target is one shared
  instruction contract, which may be reached incrementally

## Target Architecture

### 1. Backend boundary

Backend-facing identity remains explicit and distinct:

- `agent_type = astro-orchestrator`
- `agent_type = mainsequence-project-executor`

Session ownership follows those backend session identities directly. It is not a separate
runtime-profile concept.

### 2. Astro runtime profile

Astro resolves one runtime profile for the active process:

- `orchestrator`
- `project_worker`

That profile owns runtime-local behavior.

### 3. Canonical runtime contract

Both profiles should share one canonical Astro runtime contract for:

- home/config/container-data paths
- scoped Pi auth path layout
- session-state and sidecar layout

The main profile-specific filesystem difference should be the working root:

- orchestrator runtime cwd
- project-worker fixed project cwd

### 4. Request behavior

For orchestrator profile:

- request `agentType` remains meaningful within the allowed orchestrator-facing surface
- generic orchestrator routing and shared prompt behavior may still apply

For project worker profile:

- fixed runtime configuration is the primary selector
- request `agentType` is used as an assertion against the fixed worker identity
- executor-specific behavior must not depend on generic local specialist routing

## Implementation Direction

Astro should move toward the following internal structure:

1. Resolve an explicit runtime profile early in request handling.
2. Branch request execution by runtime profile, not by repeated agent-type checks.
3. Restrict generic specialist discovery to true orchestrator/specialist flows.
4. Keep backend registration and session identity logic keyed by `agent_type`.
5. Keep session metadata capable of storing backend `agentType`, while using runtime profile for
   local behavior.
6. Treat session ownership as a backend-session concern, not as a runtime-profile concern.
7. Converge orchestrator and executor deployments on one canonical Astro runtime env contract.
8. Simplify prompts into one shared instruction set with project-attached conditional behavior.

## Tasks

### Audit and preparation

- [x] Inventory all active `agentType` / `agent_type` conditionals in the stream runtime and classify
      each as backend identity, request validation, prompt behavior, cwd behavior, model behavior,
      deployment/env behavior, or historical compatibility.
- [x] Inventory all prompt sources that shape runtime behavior, including `.pi/APPEND_SYSTEM.md`,
      injected skills, and project-local `.pi/agents` extensions.
- [x] Inventory orchestrator and executor deployment env contracts in Dockerfiles, Compose, Cloud
      Build, Kubernetes/backend launch config, and docs.
- [x] Identify persisted local/session metadata fields that must remain backend identity fields
      rather than runtime-profile fields.

Audit result:

- Backend identity fields remain `agentType` / `agent_type`, backend `agentId` / `agent_id`,
  backend `agentSessionId` / `agent_session_id`, and backend session-derived `threadId` /
  `thread_id`.
- Runtime behavior is now selected through runtime profile and project attachment helpers rather
  than scattered prompt-role conditionals.
- Durable A2A metadata remains session metadata and checkpoint metadata, not prompt/runtime-role
  state.
- The repo-owned deployment contract now uses the `/home/jovyan` Astro home/config/Pi layout for
  orchestrator, local worker, Compose, Kubernetes example manifests, and docs. `Dockerfile.remote-worker`
  remains the canonical external project-worker image reference and keeps `/home/${NB_USER}`.

### Runtime profile model

- [x] Add a `RuntimeProfile` type to the stream runtime with at least `orchestrator` and
      `project_worker`.
- [x] Add a `resolveRuntimeProfile(...)` helper that derives the profile from fixed/deployed
      `agent_type` first and treats `ASTRO_EXECUTION_MODE` as topology metadata, not identity.
- [x] Validate fixed-worker env combinations early: `ASTRO_FIXED_AGENT_TYPE`,
      `ASTRO_EXECUTION_MODE`, `ASTRO_FIXED_PROJECT_CWD`, and any required project-image metadata.
- [x] Expose the resolved runtime profile in structured startup/request logs and `get_runtime_info`
      output so deployment mistakes are visible.
- [x] Keep backend `agentType` / `agent_type` unchanged in request parsing, session metadata,
      A2A envelopes, checkpoint metadata, and backend registration.

### Request handling

- [x] Make request `agentType` a backend/session assertion for fixed workers: allow it to match the
      fixed deployed `agent_type`, reject mismatches, and avoid using it as the local behavior
      selector.
- [x] Derive project attachment from runtime/profile context and session metadata, not from a
      role-specific prompt assumption.
- [x] Move cwd, repo-root, project-image, and project-id rules into project-attachment/profile
      helpers.
- [x] Remove project-worker behavior from generic specialist-routing branches.
- [x] Remove any Astro-local session ownership abstraction beyond the backend `AgentSession` and its
      `agent_type`.
- [x] Keep A2A request handling session-first: target session identity and provenance remain
      backend/session metadata, while local execution behavior follows runtime profile and project
      attachment.

Implemented in the first pass:

- `interface/stream/server.ts` now resolves and validates a `RuntimeProfile` before launching chat
  or A2A execution.
- Fixed project-worker runtimes now treat request `agentType` as an assertion against
  `ASTRO_FIXED_AGENT_TYPE`; mismatches are rejected.
- Project attachment now resolves cwd, repo root, project id, and project image from one helper
  instead of scattered role checks.
- Fixed project-worker requests no longer use generic specialist discovery as their local behavior
  selector.
- Runtime profile is emitted in startup/request logs, `/health`, available-model logs, and
  `get_runtime_info`.

### Prompt contract

- [x] Define one shared Main Sequence Astro instruction contract that covers auth, CLI failure
      handling, A2A request shape, response discipline, platform interaction, project interaction,
      workspace analysis, and implementation behavior.
- [x] Replace role-restricted prompt policy with a project-attached conditional rule:
      if attached to a prepared project cwd, treat that cwd as canonical and work in place; if not
      attached, project selection, creation, setup, platform, and workspace flows remain available.
- [x] Remove wording that says executor must never do platform/control-plane work solely because it
      is executor, or orchestrator must never do implementation solely because it is orchestrator.
- [x] Keep project-local instructions authoritative when project-attached, including project-local
      status files, task files, `.pi` instructions, and repository instructions.
- [x] Decide the concrete prompt packaging path for the shared instruction contract, then update the
      prompt arrangement without changing backend `agent_type`.
- [x] Ensure project-local prompt extensions, if still supported, extend the shared contract instead
      of replacing it with a divergent role policy.

Implemented in the prompt-contract pass:

- `.pi/APPEND_SYSTEM.md` is the single bundled Astro prompt contract.
- Project-attached behavior is selected by `ASTRO_FIXED_AGENT_TYPE=mainsequence-project-executor`
  and the runtime profile, not by a separate executor prompt file.
- The standalone executor prompt file was deleted.
- Project-local `.pi/agents` files, if present, are treated as optional specialist extensions
  rather than the core executor runtime contract.

### Prompt loading and tools

- [x] Remove executor base-prompt loading from generic specialist discovery.
- [x] Make fixed project workers load the shared instruction contract plus project-attached context
      deterministically.
- [x] Restrict generic specialist discovery to real project-local extensions, not core executor
      startup.
- [x] Revisit `ASTRO_ACTIVE_SPECIALIST` and related env markers so they do not imply executor is a
      child specialist when it is the active fixed runtime.
- [x] Make tool availability follow the shared instruction contract and runtime/project attachment,
      not duplicated role prompt files.

### Deployment and sidecar contract

- `Dockerfile.remote-worker` is the canonical reference for the project-executor runtime filesystem
  contract and should not be changed as part of this convergence. Orchestrator deployment should be
  aligned toward that home-derived contract instead.

Canonical Astro runtime filesystem contract:

- `HOME=/home/jovyan`
- `ASTRO_CONTAINER_DATA_DIR=/home/jovyan/.astro-container-data`
- `ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/jovyan/.astro-container-data/.config/mainsequence`
- `PI_CODING_AGENT_DIR=/home/jovyan/.astro-container-data/.pi/agent`
- `ASTRO_SESSION_STATE_DIR=/session-state`
- `ASTRO_STREAM_SESSION_DIR=/session-state/sessions`
- `ASTRO_SESSION_OVERRIDES_DIR=/session-state/session-overrides`
- `ASTRO_PROVIDER_CREDENTIAL_DIR=/session-state/pi-agent-auth`

- [x] Define the canonical Astro runtime filesystem contract once: `HOME`,
      `ASTRO_CONTAINER_DATA_DIR`, `ASTRO_MAINSEQUENCE_CONFIG_DIR`, `PI_CODING_AGENT_DIR`,
      `/session-state`, and session override paths.
- [x] Converge orchestrator and executor main containers on that canonical contract wherever the
      base image permits it.
- [x] Converge orchestrator and executor checkpoint sidecars on the same canonical env contract as
      their matching main containers.
- [x] Fix the project-executor sidecar path mismatch so backend auth header resolution does not try
      to write under an unwritable home directory.
- [x] Keep the effective working root as the intentional profile difference: orchestrator runtime
      cwd for non-project-attached sessions, prepared project cwd for project-attached sessions.
- [x] Update `Dockerfile`, `Dockerfile.remote-worker.local`, `docker-compose.yml`, sidecar
      deployment config, and deployment docs after the canonical contract is chosen. Do not change
      `Dockerfile.remote-worker`.

### Model and session policy

- [x] Make model-binding behavior explicit for fixed workers: backend/session model authority remains
      respected, but parent/delegating sessions must not accidentally override target session model
      policy through unrelated request state.
- [x] Keep checkpoint metadata, session metadata, history hydration metadata, and insights identity
      keyed by backend `agent_type`.
- [x] Preserve durable A2A envelope and provenance metadata independently from prompt/runtime role.
- [x] Verify that backend history hydration can still distinguish parent and delegated sessions by
      backend session identity without needing Astro-local ownership flags.

### Compatibility and cleanup

- [x] Remove stale terminology that implies two local peer agents where the intended model is one
      Astro runtime with shared instructions and project-attached behavior.
- [x] Update ADR 23, ADR 26, ADR 29, interface docs, remote-worker docs, environment docs, and prompt
      docs to match this ADR.
- [x] Decide whether older local metadata files need a one-time cleanup or migration before rollout.
- [x] Keep `agentType` / `agent_type` compatibility exactly as defined by ADR 29; do not reintroduce
      removed aliases such as `agentName`.

Cleanup result:

- No one-time local metadata migration is required. The runtime reads both camel-case and snake-case
  backend identity fields where persisted files may contain either, and writes normalized
  `agentType` / `agent_type` plus `agentSessionId` / `agent_session_id` going forward.
- The standalone `mainsequence-project-executor` prompt file has been removed from the prompt
  contract. The shared `.pi/APPEND_SYSTEM.md` contract now owns base behavior.
- ADR 29 compatibility is preserved by keeping `agentType` / `agent_type` as the identity language
  and not reintroducing request/session aliases such as `agentName`.

### Tests and verification

- [x] Verify runtime profile resolution from fixed `agent_type`, execution mode, and
      fixed project cwd.
- [x] Verify fixed workers validate request `agentType` as an assertion and do not use
      it as the local behavior selector.
- [x] Verify project-attached requests get cwd/project behavior without generic
      specialist discovery.
- [x] Verify non-project-attached requests can still use platform, project setup,
      workspace analysis, and implementation guidance from the shared prompt contract.
- [x] Verify A2A target sessions keep provenance/session metadata and do not rely on
      role-specific prompt overlays.
- [x] Verify container config confirms executor main container and sidecar share writable
      home/config/Pi/session-state paths.
- [x] Run TypeScript validation and targeted local contract smokes for non-project and
      project-attached execution paths.

Verification completed:

- `npm run check`
- `git diff --check`
- targeted `npx tsx` A2A envelope/provenance check confirming `targetAgentSessionId` survives into
  user-message provenance
- static repo checks confirming no runtime dependency on the deleted
  `.pi/agents/mainsequence-project-executor.md`
- static repo checks confirming repo-owned deployment files no longer reference `/home/appuser`
- static repo checks confirming old `agentName` aliases are not reintroduced in runtime code

## Consequences

### Positive

- Astro keeps the backend contract that depends on distinct `agent_type` values.
- Session ownership stays simple because it continues to follow backend session identity directly.
- Executor runtime behavior becomes easier to reason about and harder to accidentally route through
  orchestrator-only logic.
- Orchestrator and executor deployments become easier to operate because Astro runtime env/path
  rules converge instead of drifting.
- Prompt maintenance becomes easier because shared Main Sequence runtime rules live in one place
  instead of being duplicated across role-specific overlays.
- Capability behavior becomes less arbitrary because project attachment, rather than runtime role
  alone, explains the main user-visible difference.
- Fixed worker deployment assumptions have one explicit home.
- Prompt loading, project cwd behavior, model policy, and sidecar parity become cleaner to audit.

### Negative

- The stream runtime gains one new internal abstraction that must be used consistently.
- Some existing code that currently branches on `agentType` will need to be reorganized rather than
  renamed in place.

### Migration note

This ADR is an internal Astro refactor direction. It does not require changing backend-visible
`agent_type` values or frontend request contracts before implementation begins.
