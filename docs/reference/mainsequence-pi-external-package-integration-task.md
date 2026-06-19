# Implementation Task: Integrate External `@mainsequence/pi` Package

Status: Proposed
Parent ADR: [ADR 40: General Pi Deployment Runtime Package Boundary](./adr-40-general-pi-deployment-runtime-package-boundary.md)
Implementation Status: Local package-shape simulation exists under `tmp_ms_pi`; Astro can consume it
through `ASTRO_PI_PACKAGE_PATHS`.

This task defines how Astro should consume an independent Main Sequence Pi package. Astro must not
be the source of truth for that package.

`tmp_ms_pi` is a temporary local simulation of the package contract. It is not the final package
location and must not become the source of truth.

## Goal

Make Main Sequence work with Pi independently of Astro by using an external portable Pi package:

```text
@mainsequence/pi
  Portable Main Sequence Pi package sourced from the Main Sequence SDK repo or a sibling
  Main Sequence repo.
```

Astro may install, load, or materialize this package when running in Main Sequence mode, but Astro
does not own its skills, prompts, component maps, or portable Pi extension behavior.

## Source Of Truth

The source of truth for portable Main Sequence Pi behavior should be one of:

- `mainsequence-sdk`, under Pi-package/resource locations such as `agent_scaffold/skills/**` and
  `agent_scaffold/prompts/**`
- a sibling Main Sequence-owned repository dedicated to Pi integration

The package may be published as `@mainsequence/pi`, but its content must be maintained outside the
Astro repository.

## Astro Responsibilities

Astro may own:

- configuration for which external Pi package to load in Main Sequence deployments
- runtime materialization of the external package into the Pi environment
- version pinning or image installation of the external package
- compatibility checks that the package resolves through Pi's package loader
- adapter-owned runtime skills that require Astro/Main Sequence backend authority

Astro must not own:

- Main Sequence project-builder/component taxonomy
- SDK component maps
- portable Main Sequence skills/prompts
- portable Main Sequence Pi TypeScript extensions
- package publication for `@mainsequence/pi`

## Non-Goals

This task must not:

- create `packages/mainsequence-pi` inside Astro
- make Astro the source of truth for SDK-owned skills/prompts
- duplicate `project_builder` or component-map content in Astro
- move Main Sequence backend/session/checkpoint/credential behavior into the Pi package
- change public endpoint behavior as part of package separation
- replace `tmp_ms_pi` with a real external package before the final migration phase
- publish `@mainsequence/pi`

## Required External Package Shape

The external package should follow Pi package conventions:

```json
{
  "name": "@mainsequence/pi",
  "keywords": ["pi-package"],
  "type": "module",
  "pi": {
    "prompts": ["./pi/prompts"],
    "extensions": ["./pi/extensions"]
  }
}
```

The package does not directly declare SDK-owned `pi/skills` while Astro is using the current
Main Sequence SDK scaffold-copy flow. Instead, the package should provide a Pi extension that uses
`resources_discover` to seed `.agents/skills` through the SDK-owned copy/export command when the
runtime cwd does not already contain `.agents/skills`.

## Required External Package Contents

The external package should delegate component and project-construction knowledge to the SDK-owned
project builder skill:

```text
https://github.com/mainsequence-sdk/mainsequence-sdk/tree/main/agent_scaffold/skills/project_builder
```

Do not duplicate that component map inside Astro.

## SDK Skill Injection Facts

Astro must not hardcode individual SDK skill slugs during stream bootstrap. The old direct
materialization path has been removed:

```text
mainsequence skills path command_center/workspace_analysis
mainsequence skills path a2a_communication
```

The package simulation replaces those direct hard-coded delivery paths with one package-owned
extension flow:

```text
tmp_ms_pi/pi/extensions/hooks/scaffold-skill-discovery
  resources_discover
  if <cwd>/.agents/skills exists: no-op
  else:
    source = mainsequence skills path
    copy source into <cwd>/.agents/skills/mainsequence
```

This keeps the SDK as the source of truth for the skill set, avoids project `.venv` requirements in
orchestrator runtimes, and allows Pi to see every skill exported by the installed SDK rather than a
two-skill allowlist.

The package simulation should still be compared against these existing delivery paths before any
runtime cutover:

- project-executor image/package contents
- session capability materialization
- runtime bootstrap settings generated under `.pi`
- SDK-owned `agent_scaffold/skills/**`
- SDK-owned `agent_scaffold/prompts/**`

## Cutover Consideration

For any SDK-owned skill or prompt moved into the package, decide what happens to the current
delivery path:

```text
runtime bootstrap materialization
external @mainsequence/pi package loading
```

This is not a new runtime rule. It is just the practical cutover question needed to avoid confusing
the agent with duplicate skills.

## Astro Integration Steps

The implementation must prove package composition locally before replacing the simulation with a
real external source. `tmp_ms_pi` remains the active test package until the final replacement step.

### Phase 1: Env-Controlled Package Composition

Add a deployment-controlled package path, for example:

```text
ASTRO_PI_PACKAGE_PATHS=/app/tmp_ms_pi
```

The value points to Pi package directories that the deployment wants Astro to load. During local
simulation this points at `tmp_ms_pi`. Later, the same mechanism can point at an installed
Main Sequence-owned package.

Astro Core must not hardcode `tmp_ms_pi`, `@mainsequence/pi`, or Main Sequence package paths.
Main Sequence deployment configuration owns the env value.

The generated runtime Pi settings should be composed from:

```text
Astro Core package/resources
ASTRO_PI_PACKAGE_PATHS entries
```

This phase proves that Main Sequence Pi behavior can be injected by deployment composition rather
than being baked into Astro's root `pi/` tree.

### Phase 2: Orchestrator-Only Simulation Cutover

Use `tmp_ms_pi` first for orchestrator-facing Main Sequence Pi resources only.

Candidate resources are:

- orchestrator prompts
- Main Sequence SDK/platform skills
- Main Sequence A2A/workspace-analysis skills currently materialized by bootstrap
- portable Main Sequence hooks/tools that do not import Astro runtime internals

Do not add project-executor resources to `tmp_ms_pi` in this phase. Project-executor has a separate
inventory because it is currently a Main Sequence project-attached deployment composition, not just
a portable Pi package.

### Phase 3: Remove Duplicate Root `pi/` Delivery

After `tmp_ms_pi` provides a resource and Pi can load it through `ASTRO_PI_PACKAGE_PATHS`, remove
that resource from Astro root delivery.

The desired end state for Astro root `pi/` is:

```text
pi/
  Astro-neutral resources only, if any
```

Main Sequence product behavior should live in `tmp_ms_pi` during the simulation. If no Astro-neutral
Pi resources remain, Astro Core should eventually be able to run without a root `pi/` package entry.

### Phase 4: Replace Bootstrap SDK Skill Materialization

Implementation status: completed for the local `tmp_ms_pi` simulation.

Direct startup materialization such as:

```text
mainsequence skills path command_center/workspace_analysis
mainsequence skills path a2a_communication
```

has been replaced with package loading through `ASTRO_PI_PACKAGE_PATHS` plus the package-owned
`resources_discover` skill seeding hook.

If `tmp_ms_pi` seeds SDK-owned skills, Astro should not also materialize the same skills through
bootstrap. The goal is one delivery path per resource, with the SDK/CLI owning the actual copy
rules.

### Phase 5: Keep Backend Concerns Out Of The Pi Package

Main Sequence backend/runtime behavior must stay outside `tmp_ms_pi`.

Those concerns belong behind the Main Sequence Astro adapter and deployment configuration:

```text
ASTRO_BACKEND=mainsequence
```

Adapter-owned concerns include:

- `AgentSession.uid`
- runtime credential auth
- checkpoint lease, restore, renew, flush, and cancel
- provider credential hydration
- capability binding lookup
- backend A2A/session authority
- project-executor identity, project attachment, and prepared image/cwd policy

### Phase 6: First Proper Main Sequence Deployment Shape

The first successful separation deployment should run as:

```text
Astro Core image/runtime
ASTRO_BACKEND=mainsequence
ASTRO_PI_PACKAGE_PATHS=/app/tmp_ms_pi
```

Success criteria:

- Astro starts without Main Sequence product behavior hardcoded in root `pi/`.
- `tmp_ms_pi` provides the selected Main Sequence Pi resources.
- SDK skill materialization is not duplicated for resources supplied by `tmp_ms_pi`.
- Public endpoints keep current behavior.
- Project-executor remains unchanged unless its separate inventory has accepted specific portable
  package content.

### Final Phase Only: Replace The Simulation

Do not replace `tmp_ms_pi` with the real external package until the separation is otherwise proven.

At the end of the migration, change only the package source/path, for example:

```text
ASTRO_PI_PACKAGE_PATHS=/node_modules/@mainsequence/pi
```

or another installed package location chosen by the Main Sequence deployment. Astro Core should not
care whether the package comes from `tmp_ms_pi`, the SDK repo, npm, or a mounted volume.

## Validation

Astro-side validation should prove only consumption, not ownership:

```bash
pi install <external @mainsequence/pi source>
pi list
```

or a programmatic package resolver check against the external package source.

Expected result:

- Pi sees the external package's skills/prompts/extensions.
- The test output makes it clear whether a skill came from bootstrap materialization or package
  loading.
- Astro root `pi/` contains only runtime-owned Pi resources.
- Astro does not contain a copied package under `packages/mainsequence-pi`.

## Completion Criteria

This task is complete only when:

- an external source-of-truth repository/package is identified
- existing orchestrator and project-executor skill/prompt injection has been understood
- package testing shows which resources are delivered by the simulated package
- Astro has a documented way to consume it
- Astro does not duplicate its contents
- Astro root `pi/` no longer duplicates resources delivered by `tmp_ms_pi`
- the Main Sequence Astro adapter remains responsible for backend/runtime-only behavior
