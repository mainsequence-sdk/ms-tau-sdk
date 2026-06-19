# ADR 39: Unified Pi Runtime Context

Status: Proposed
Date: 2026-06-19
Implementation Status: Not implemented

This ADR records the target runtime shape that should precede the broader Astro package-boundary
split in ADR 40.

## Context

Astro currently exposes Main Sequence backend-visible identities such as:

- `astro-orchestrator`
- `project-executor`

Historically, those identities leaked into local runtime behavior. The stream server, bootstrap
code, docs, and deployment settings started treating them as two different Astro runtime
architectures:

- `astro-orchestrator` as a control-plane runtime with no fixed project cwd by default
- `project-executor` as a fixed project runtime that requires a prepared project cwd

That distinction is too heavy. It makes the code branch on product role names when the real runtime
difference is just which workspace Pi is started in and which skill layers are available.

The intended product model is simpler:

- the same Astro runtime can run with or without a prepared project cwd
- an `astro-orchestrator` session may also run inside a fixed cwd
- a project-attached runtime may still use the same Main Sequence package skills as the orchestrator
- project-local `.agents/skills` may add or override behavior when they exist
- backend `agent_type` remains useful metadata, but it must not decide the local runtime
  architecture

In other words, the orchestrator is an edge case of the same Pi runtime with no user project
attached.

## Problem

The current split creates unnecessary implementation noise:

- runtime profile names mirror Main Sequence product roles instead of describing runtime state
- request handling checks `project-executor` when it often means "has fixed cwd"
- bootstrap names and paths imply an orchestrator-only runtime workspace
- docs describe the executor as a separate runtime architecture even though the prompt and Pi launch
  mechanics are already converging
- package-boundary work becomes harder because Astro Core appears to own Main Sequence product roles

This also blocks the cleaner separation in ADR 40. Astro Core should be a general Pi deployment
runtime. Main Sequence roles should be supplied by adapter/session metadata and package skill
layers, not by hard-coded Astro runtime branches.

## Decision

Astro should converge on one internal runtime shape:

```text
Pi runtime workspace
  cwd
  package/runtime skills
  optional project-local skills
  optional project files
  optional backend session
```

The local runtime distinction is:

```text
project_attached = true | false
```

not:

```text
runtime_kind = astro-orchestrator | project-executor
```

Backend-visible `agent_type` values remain valid, but they are not Astro Core runtime kinds.

## Target Runtime Context

Astro should resolve one runtime context before launching Pi:

```ts
type RuntimeContext = {
  cwd: string;
  projectAttached: boolean;
  projectId: string | null;
  projectImageRef: string | null;
  backendAgentType: string | null;
  backendAgentSessionUid: string | null;
  skillLayers: RuntimeSkillLayer[];
};
```

The important fields are:

- `cwd`: the directory where Pi runs.
- `projectAttached`: whether this cwd represents a prepared user/project workspace.
- `projectId`: optional backend project identity.
- `projectImageRef`: optional image identity for prepared project workers.
- `backendAgentType`: backend/session identity such as `astro-orchestrator` or `project-executor`.
- `backendAgentSessionUid`: backend session uid when a backend session exists.
- `skillLayers`: ordered skill/prompt/tool layers available to this runtime.

The current names can still appear in logs and backend payloads, but Astro Core should route local
runtime behavior through this context.

## Skill Layering

Skill injection should be the real extension point.

Target layering:

```text
1. Astro Core runtime skills
2. Main Sequence package skills
3. backend/session-provided capability skills
4. project-local .agents/skills, if present
```

Project-local skills may override or augment package skills according to Pi's normal package/project
precedence. That is acceptable and expected.

This means the difference between "orchestrator" and "executor" is not a separate prompt/runtime
architecture. The difference is whether the runtime context includes a prepared project cwd and
project-local skill layer.

## Orchestrator As No-Project Runtime

The default orchestrator deployment becomes:

```text
cwd = default Astro runtime workspace
projectAttached = false
skillLayers = Astro Core + Main Sequence package
backendAgentType = astro-orchestrator
```

If the orchestrator is launched with a fixed cwd, that is also valid:

```text
cwd = configured workspace
projectAttached = true or false depending on project metadata
skillLayers = Astro Core + Main Sequence package + project-local skills when present
backendAgentType = astro-orchestrator
```

`astro-orchestrator` must therefore not imply "no fixed project cwd."

## Project-Attached Runtime

A project-attached runtime becomes:

```text
cwd = prepared project workspace
projectAttached = true
skillLayers = Astro Core + Main Sequence package + project-local skills
backendAgentType = astro-orchestrator | project-executor | custom adapter identity
```

`project-executor` may remain a backend/session identity, but it must not imply a separate Astro
runtime architecture. It is one possible backend label for a project-attached runtime.

## What Must Stop Being Runtime-Core Logic

Astro Core should stop treating these as architectural branches:

- `agent_type=astro-orchestrator` means no fixed project cwd
- `agent_type=project-executor` means fixed project cwd is required
- executor and orchestrator need separate Pi launch paths
- executor and orchestrator need separate base prompt architectures
- project attachment should be inferred from product role name

Instead:

- cwd comes from runtime context resolution
- project attachment is derived from configured/requested/backend project context
- skills come from ordered runtime/package/session/project layers
- backend `agent_type` is metadata and adapter/session policy

## Relationship To Backend Identity

This ADR does not delete backend identities.

Main Sequence may continue using:

```text
astro-orchestrator
project-executor
```

for backend `Agent.agent_type`, session ownership, analytics, routing, and product policy.

The change is internal to Astro Core: those names must not be used as separate local runtime
architectures.

## Relationship To ADR 40

ADR 40 splits Astro Core from Main Sequence packages and adapters.

This ADR must come first because it defines the Astro Core boundary:

- Astro Core owns the generic Pi runtime context.
- Main Sequence package/adapters may provide skill layers, backend session policy, and product
  metadata.
- Main Sequence role names must not leak into Astro Core as hard-coded runtime architectures.

## Migration Direction

The migration should happen in small steps:

- introduce a runtime-context concept beside the current runtime-profile code
- rename bootstrap concepts away from orchestrator-only names where they describe generic runtime
  workspaces
- replace `project-executor` conditionals with `projectAttached`, `fixedCwd`, or adapter-policy
  checks where that is what the branch actually means
- keep backend `agent_type` validation at the backend/session boundary
- keep public API compatibility while removing local runtime branching by role name
- only after this is stable, continue the ADR 40 package/adapter split

## Non-Goals

- Do not remove Main Sequence backend `agent_type`.
- Do not remove `astro-orchestrator` or `project-executor` from backend/session payloads.
- Do not require a public runtime attachment protocol.
- Do not make project-local skills mandatory.
- Do not move all Main Sequence package/adapter code in this ADR.
- Do not change the public A2A contract in this ADR.

## Consequences

Positive consequences:

- Astro Core becomes a real Pi deployment runtime instead of a Main Sequence role runtime.
- Orchestrator and project-attached execution share the same Pi launch and skill-layer model.
- Main Sequence-specific behavior can move into package/adapters without duplicating runtime code.
- The project-executor/orchestrator distinction becomes product metadata, not runtime architecture.

Tradeoffs:

- Existing docs and code that use `RuntimeProfileKind` need careful migration.
- Some current validation rules must be reclassified as adapter policy or runtime-context validation.
- Logs should keep backend identity while adding clearer runtime-context fields such as
  `projectAttached` and `cwd`.
