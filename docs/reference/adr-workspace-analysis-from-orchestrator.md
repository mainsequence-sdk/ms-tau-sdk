# ADR: Workspace Analysis From Orchestrator

## Status

Accepted. Updated by ADR 40 package-boundary work.

The capability remains accepted, but the implementation is no longer deterministic two-skill
startup materialization. Workspace-analysis is now delivered through the Main Sequence Pi package
simulation and SDK-owned skill discovery.

## Context

Astro's parent agent currently has strong routing for:

- Main Sequence platform interaction
- Main Sequence project creation and setup
- `mainsequence-sdk` questions

What is still underspecified is a first-class **workspace analysis** capability owned by the
orchestrator itself.

By workspace analysis, we mean a read-oriented flow where the orchestrator can:

- inspect a Main Sequence workspace
- load the relevant Main Sequence skill for that analysis
- read local instructions or status files when needed
- summarize structure, readiness, blockers, and next actions

This is intentionally different from:

- project creation
- project-local implementation
- project-scoped specialist handoff

The current gap is not only prompt wording. It is also a startup/runtime concern.

Today, if the orchestrator must discover or load its Main Sequence workspace-analysis skill lazily during
the first user turn, several bad outcomes follow:

- the first turn pays visible setup latency
- the user sees hidden runtime preparation as if it were normal reasoning/tool work
- the orchestrator may behave inconsistently depending on whether that context was already present
- workspace analysis becomes coupled to conversational skill discovery instead of deterministic
  runtime readiness

The desired behavior is for the workspace-analysis skill to be ready **before Astro begins serving
requests**, so the orchestrator can use it immediately and consistently.

## Decision

Astro will treat **workspace analysis** as a first-class orchestrator capability.

This capability is owned directly by `astro-orchestrator`. It is not a project-implementation
workflow and must not depend on project-scoped specialist routing.

Astro implements workspace analysis as a real Pi skill and ensures that skill is available through
the Main Sequence SDK-owned skill surface.

That means:

1. The orchestrator prompt contract in `.pi/APPEND_SYSTEM.md` will explicitly include workspace
   analysis as an allowed capability.
2. The Main Sequence Pi package simulation will seed SDK skills when Pi loads the package extension,
   resolve the installed SDK skill root with `mainsequence skills path`, copy that full tree into
   `<cwd>/.agents/skills/mainsequence`, and return `<cwd>/.agents/skills` during
   `resources_discover`.
3. The orchestrator will load and follow that injected skill at request time instead of inventing
   an ad hoc analysis workflow in the prompt.
4. For a concrete workspace, the orchestrator must obtain the canonical analysis input with
   `mainsequence cc workspace snapshot <workspace_id>` and treat the resulting snapshot files as
   the source of truth for the analysis.

## Scope

This ADR is only about:

- orchestrator capability definition
- orchestrator startup/runtime preparation
- SDK/package-owned discovery of the workspace-analysis skill
- user-facing workspace-analysis behavior

This ADR is not about:

- project-local implementation behavior
- specialist handoff rules
- project-coder bootstrap
- backend session ownership

## Runtime Contract

Before the orchestrator handles analysis work, Pi must be able to discover the SDK-provided
workspace-analysis skill without additional conversational setup.

At minimum, runtime setup must:

- load the Main Sequence Pi package through `ASTRO_PI_PACKAGE_PATHS`
- run the package-owned SDK skill discovery extension
- avoid hardcoding individual SDK skill slugs in Astro bootstrap code
- leave the SDK/CLI responsible for copying all exported SDK skills into the runtime-visible
  `.agents/skills` tree
- preserve observability through package/runtime readiness logs

Skill readiness may be represented by one or both of:

- package discovery logs
- the runtime `.agents/skills` directory itself

The important rule is not the storage mechanism. The important rule is that the orchestrator must
not guess whether the workspace-analysis skill exists.

## Prompt Contract

`.pi/APPEND_SYSTEM.md` should explicitly describe workspace analysis as an orchestrator-owned
capability.

That prompt contract should make the following clear:

- workspace analysis is allowed
- it is handled directly by the orchestrator
- it is read-oriented by default
- it should load and follow the injected `command_center/workspace_analysis` skill before broader
  local inspection
- it should always obtain `mainsequence cc workspace snapshot <workspace_id>` before analyzing a
  concrete workspace
- it should treat snapshot files as canonical analysis input
- it should not use raw workspace-detail payloads as the analysis source once a workspace id is
  known
- it should not require project creation or project-implementation routing

The prompt should also distinguish workspace analysis from:

- creating a new Main Sequence project
- switching into project-local implementation
- answering generic non-Main-Sequence repository questions

## Startup Placement

The deterministic preload belongs in Astro startup/bootstrap, not in user-turn reasoning.

The intended implementation layer is the pre-server startup path, currently centered around:

- `bin/astro-stream.ts`
- `runtime/bootstrap/pi-agent-dir.mjs`

This keeps workspace-analysis skill availability aligned with other runtime-managed setup behaviors and
avoids making the first user turn responsible for hidden environment preparation.

## Failure Behavior

If the workspace-analysis skill cannot be materialized during bootstrap, Astro should fail startup
instead of continuing in a degraded mode.

The orchestrator must not start serving requests when the required skill copy step failed.

## Observability

Bootstrap should emit a structured readiness event for workspace analysis.

That event should include enough detail to answer:

- was the workspace-analysis skill materialized successfully
- what command resolved the source skill path
- where the source skill path was resolved from
- where the skill was copied inside the runtime Pi skill tree

This readiness signal should exist before the first chat request is handled.

## Consequences

### Positive

- workspace analysis becomes a clear orchestrator capability instead of an accidental side effect
- first-turn behavior becomes faster and more predictable
- the user sees less hidden setup work inside the conversation
- orchestrator behavior becomes easier to reason about and debug
- runtime readiness for workspace analysis becomes observable
- workspace analysis becomes grounded in one canonical CLI snapshot path instead of drifting across
  raw workspace payload shapes

### Negative

- Main Sequence package loading must be configured correctly for deployments that need this skill
- package, prompt, and SDK skill contracts must stay in sync

## Open Questions

- what additional validation should the SDK skill discovery hook report when SDK skill copying
  succeeds but the expected skill is absent
- whether future Main Sequence SDK skills need package-level enablement filters

## Verification Plan

- confirm the Main Sequence Pi package is present in generated Pi settings through
  `ASTRO_PI_PACKAGE_PATHS`
- confirm the package-owned extension seeds SDK skills into `.agents/skills`
- confirm the orchestrator can answer a workspace-analysis request by following the injected skill
  without first performing user-visible skill discovery
- confirm the orchestrator stays within the workspace-analysis scope defined in
  `.pi/APPEND_SYSTEM.md`
- confirm SDK skill discovery failure is surfaced as a package/resource discovery error

## Tasks

- [x] Update `.pi/APPEND_SYSTEM.md` to define `workspace analysis` as an explicit
  `astro-orchestrator` capability.
- [x] Add orchestrator prompt rules that distinguish workspace analysis from project creation,
  project implementation, and generic non-Main-Sequence repository analysis.
- [x] Remove direct Astro bootstrap materialization for individual Main Sequence SDK skills.
- [x] Load the Main Sequence Pi package through `ASTRO_PI_PACKAGE_PATHS`.
- [x] Delegate SDK skill seeding to the package-owned discovery extension.
- [x] Route capability 4 in `.pi/APPEND_SYSTEM.md` to the injected
  `command_center/workspace_analysis` skill instead of inventing a separate prompt-defined
  workflow.
- [x] Emit structured startup readiness for package/runtime settings.
- [x] Surface SDK skill discovery failures through Pi package resource discovery.
- [x] Add or update docs describing the startup preload contract and the orchestrator-owned
      workspace-analysis flow.
- [ ] Add verification coverage or a scripted smoke check that proves the workspace-analysis skill
  ready before the first chat request.
