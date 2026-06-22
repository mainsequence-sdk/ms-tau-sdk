# ADR 39: Unified Pi Runtime Context

Status: Accepted
Date: 2026-06-19
Implementation Status: Implemented in Astro runtime on 2026-06-20.

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

## Implementation Tasks

- [x] Replace the old runtime-profile kind implementation with a `RuntimeContext` shape that
  includes `cwd`, `projectAttached`, `projectId`, `projectImageRef`, `backendAgentType`,
  `backendAgentSessionUid`, and `skillLayers`.
- [x] Add `resolveRuntimeContext(...)` so `cwd` and `projectAttached` come from configured runtime
  state instead of deriving local runtime architecture from `agent_type`.
- [x] Stop inferring `RuntimeProfileKind="project-executor"` from `ASTRO_FIXED_PROJECT_CWD`.
  A fixed cwd now means project attachment, not a separate runtime kind.
- [x] Allow `astro-orchestrator` backend sessions to run with `ASTRO_FIXED_PROJECT_CWD` when the
  backend/session policy permits it.
- [x] Reclassify `ASTRO_FIXED_AGENT_TYPE` as backend/session identity policy, not required local
  runtime architecture selection.
- [x] Remove new-deployment reliance on `ASTRO_EXECUTION_MODE=remote_project_worker`; keep it only
  as tolerated legacy topology metadata for old deployments.
- [x] Replace `project-executor` conditionals in request handling with checks for
  `projectAttached`, fixed cwd, backend agent/session policy, or project metadata.
- [x] Replace `resolveProjectAttachment(...)` logic that treated
  `agentType === "project-executor"` as image-backed project execution. Prepared project behavior
  now comes from runtime context fields such as `projectAttached`, `fixedProjectCwd`, and
  `projectImageRef`.
- [x] Preserve the prepared project runtime behavior where `projectId` may be omitted when the
  runtime is already pinned to a prepared image/cwd, but move that rule onto runtime
  context/project-attachment policy.
- [x] Replace `fixedWorkerRequiresBackendSessionAuthority` checks based on
  `runtimeProfile.kind === "project-executor"` with prepared-project/session-authority policy on
  the runtime context.
- [x] Preserve backend-authority-first model binding for prepared project-attached runtimes while
  expressing that as runtime context/session-authority policy instead of `project-executor` role
  logic.
- [x] Remove the `project-executor` exception around `ensureMainsequenceCliAuthReady()`. Main
  Sequence CLI auth preflight now runs for no-project and project-attached runtimes when the Main
  Sequence adapter/package is active.
- [x] Remove the deprecated semantic Agent identity special handling from the unified runtime
  context path.
- [x] Reclassify A2A current-agent identity resolution so defaulting to `astro-orchestrator` or
  using `ASTRO_FIXED_AGENT_TYPE` is backend/session identity resolution, not local runtime-context
  selection.
- [x] Replace validation messages and error codes based on runtime profile with runtime-context
  validation for missing or invalid fixed cwd/project attachment.
- [x] Rename bootstrap helpers and generated paths that describe generic runtime workspaces as
  orchestrator-only, including `ensureOrchestratorRuntimeProject(...)`, while keeping existing
  path/env fallbacks such as `astro-orchestrator-runtime` working.
- [x] Rewrite Main Sequence package prompt branches that used
  `ASTRO_FIXED_AGENT_TYPE=project-executor` to describe project-attached vs non-project-attached
  runtime behavior.
- [x] Update launch/preflight logging to include both backend identity and runtime context fields:
  `backendAgentType`, `projectAttached`, `cwd`, `projectId`, and `projectImageRef`.
- [x] Update Astro-owned deployment env shapes so project-attached deployments only require
  `ASTRO_FIXED_PROJECT_CWD`; legacy backend env combinations remain tolerated.
- [x] Keep `BUILD_AGENTS_IN_BACKEND`, `MAINSEQUENCE_BACKEND`,
  `ASTRO_MAINSEQUENCE_CONFIG_DIR`, and `MAINSEQUENCE_PROJECTS_BASE` classified as Main Sequence
  adapter/session-authority env, not Astro Core runtime identity env.
- [x] Ensure `ASTRO_PI_PACKAGE_PATHS` is present in no-project and project-attached Astro-owned
  deployment images/examples so package skills load consistently.
- [x] Update current docs that described `project-executor` as a separate local runtime
  architecture so they describe backend identity plus project attachment instead.
- [x] Update `Dockerfile.remote-worker` and deployment examples so new project-attached deployments
  do not require `ASTRO_EXECUTION_MODE=remote_project_worker`; old env combinations remain
  accepted where supplied by existing deployments.
- [x] Add focused regression tests for the unified runtime-context contract, project-attached image
  env, and preserved Main Sequence `project-executor` unique-id behavior.
- [x] Mark this ADR implemented after code no longer uses backend role names as the deciding local
  runtime architecture branch.

## Backward Compatibility Requirements

The migration must preserve existing deployed behavior while removing the internal role/runtime
coupling.

- Existing backend sessions with `agent_type=astro-orchestrator` remain valid.
- Existing backend sessions with `agent_type=project-executor` remain valid.
- Existing requests that send `agentType="project-executor"` continue to work.
- Existing requests that send `agentType="astro-orchestrator"` continue to work.
- Existing `Dockerfile.remote-worker` images that set `ASTRO_EXECUTION_MODE=remote_project_worker`,
  `ASTRO_FIXED_AGENT_TYPE=project-executor`, and `ASTRO_FIXED_PROJECT_CWD` continue to boot.
- Existing prepared image-backed project runtimes may still omit `projectId` when a fixed cwd/image
  context already supplies the project workspace.
- Existing Main Sequence backend identity behavior for `project-executor`, including the stable
  adapter-level unique id currently produced as `project-executor`, is preserved.
- Existing backend-owned session hydration, checkpoint, provider credential, capability, and model
  binding behavior remains available for both no-project and project-attached runtimes.
- Existing public A2A request/response contracts remain unchanged in this ADR.
- New deployments should use the unified env shape, but old env combinations must be tolerated
  until a separate deprecation ADR or migration removes them.

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
