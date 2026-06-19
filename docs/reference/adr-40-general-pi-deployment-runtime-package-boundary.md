# ADR 40: General Pi Deployment Runtime Package Boundary

Status: Proposed
Date: 2026-06-19
Implementation Status: Partially implemented. A temporary local simulation exists under
`tmp_ms_pi`, Astro can consume additional Pi packages through `ASTRO_PI_PACKAGE_PATHS`, and root
`pi/` no longer carries the Main Sequence product skills/prompts/tools now supplied by the
simulation. No production external package has been created and Astro has not been cut over to a
separate backend adapter split.

This ADR depends on
[ADR 39: Unified Pi Runtime Context](./adr-39-unified-pi-runtime-context.md). ADR 39 defines the
internal runtime shape that prevents Main Sequence deployment identities from becoming separate
Astro Core runtime architectures.

This ADR records the current-state-aware boundary for turning Astro into a more general Pi
deployment runtime without pretending that the current repository already has that split.

The current codebase still ships as one `astro` package that contains:

- the HTTP/SSE/REST runtime around Pi
- Main Sequence backend/session/checkpoint/credential code
- Main Sequence runtime/bootstrap logic

Main Sequence Pi product resources are currently represented by the local `tmp_ms_pi` simulation,
not by a production external package.

The proposed direction is to split those responsibilities along Pi-native package boundaries and
Astro backend-adapter boundaries.

## Implementation Task Documents

This ADR is an architecture proposal. Concrete implementation work must be tracked in separate task
documents.

- [Integrate External `@mainsequence/pi` Package](./mainsequence-pi-external-package-integration-task.md)
  defines how Astro consumes a Main Sequence-owned Pi package without becoming the source of truth
  for that package.

## Current Decisions This ADR Must Preserve

This ADR is not designing from a blank slate. It must preserve the current decisions already
recorded in the active reference set:

- Backend sessions are created outside Astro. Astro attaches to existing backend sessions; chat and
  A2A do not create sessions.
- Runtime identity is currently represented as `agentType` / `agent_type`, and the currently exposed
  Main Sequence runtime/backend types are `astro-orchestrator` and `project-executor`. Per ADR 39,
  those names are backend/session metadata and package/adapter policy inputs, not separate Astro
  Core runtime architectures.
- Backend resource lookup uses `uid`, not legacy numeric ids.
- Public A2A must not use the superseded public runtime attachment workflow. Public A2A continues
  through `POST /api/a2a/v1/message:send`, with the backend session represented through A2A
  `message.contextId` when the caller is authorized to use that uid.
- Warm A2A runners are internal Astro execution machinery. They are not a public attach/status
  protocol.
- Stateless LLM passthrough is a separate endpoint, `POST /api/llm/chat`, and must not go through
  sessions, checkpoints, Pi runners, capabilities, project attachment, queues, or persistence.
- Main Sequence runtime credential auth remains the deployed-runtime auth mechanism.
- Main Sequence session capabilities and injected skills remain current behavior until an adapter
  split replaces the direct imports.

## Current Public Runtime Surface

Current server startup advertises these primary endpoints:

```text
POST /api/llm/chat
POST /api/chat
POST /api/a2a/v1/message:send
```

Current A2A route matching also includes the standard route family under `/api/a2a/v1` and JSON-RPC
under `/api/a2a/rpc`:

```text
POST /api/a2a/v1/message:send
POST /api/a2a/v1/message:stream
GET  /api/a2a/v1/tasks
GET  /api/a2a/v1/tasks/{task_id}
POST /api/a2a/v1/tasks/{task_id}:cancel
POST /api/a2a/v1/tasks/{task_id}:subscribe
POST /api/a2a/v1/tasks/{task_id}/pushNotificationConfigs
GET  /api/a2a/v1/tasks/{task_id}/pushNotificationConfigs
GET  /api/a2a/v1/tasks/{task_id}/pushNotificationConfigs/{config_id}
GET  /api/a2a/v1/extendedAgentCard
POST /api/a2a/rpc
```

This ADR must not reintroduce public endpoints such as:

```text
POST /api/a2a/sessions/{uid}/runtime
POST /api/a2a/sessions/{uid}/runtime/chat
```

Those were part of the superseded public runtime attachment direction. If similar mechanics exist
internally, they are implementation details, not the public A2A client contract.

## Current Repository Shape

The current `astro` package is also a Pi package. `package.json` declares:

```json
{
  "pi": {
    "extensions": [
      "./pi/extensions/hooks/agent-registration",
      "./pi/extensions/hooks/session-model",
      "./pi/extensions/hooks/telemetry",
      "./pi/extensions/tools/runtime-info"
    ]
  }
}
```

Current `.pi/settings.json` loads this repository as a package:

```json
{
  "packages": [
    "..",
    "../node_modules/pi-web-access"
  ]
}
```

No production Main Sequence Pi package exists today. The proposed package boundary in this ADR is a
future extraction target, not current implementation. A local `tmp_ms_pi` directory may be used only
to simulate the package contract before the real package is moved to the SDK repo or a sibling Main
Sequence repo.

Current Astro root Pi resources in this repository are:

```text
.pi/APPEND_SYSTEM.md
.pi/settings.json
pi/extensions/hooks/agent-registration/index.ts
pi/extensions/hooks/session-model/index.ts
pi/extensions/hooks/telemetry/index.ts
pi/extensions/shared/structured-logging.ts
pi/extensions/shared/telemetry.ts
pi/extensions/tools/runtime-info/index.ts
pi/types/pi-stubs.d.ts
```

The local Main Sequence Pi package simulation currently carries the Main Sequence product resources:

```text
tmp_ms_pi/pi/extensions/hooks/project-policy/index.ts
tmp_ms_pi/pi/extensions/hooks/scaffold-skill-discovery/index.ts
tmp_ms_pi/pi/extensions/tools/mainsequence-cli-auth/index.ts
tmp_ms_pi/pi/extensions/tools/mainsequence-runtime-info/index.ts
tmp_ms_pi/pi/prompts/review-main-sequence-project.md
tmp_ms_pi/pi/prompts/verify-mainsequence-tutorial.md
tmp_ms_pi/pi/system/APPEND_SYSTEM.md
```

Current server/runtime code directly imports Main Sequence/backend functionality:

```text
interface/stream/server.ts
  imports adapters/mainsequence/runtime-auth.ts
  imports interface/stream/mainsequence-agent-registration.ts
  imports session-checkpoint-client.ts
  imports model-provider-credentials-client.ts
  imports session-capabilities.ts
```

Current bootstrap is also mixed:

```text
bin/astro-stream.ts
  -> bootstrapPiAgentDir()
     -> materialize runtime .pi settings and cwd
     -> compose configured Pi package settings and system prompts
     -> create Main Sequence CLI config link
     -> create managed mainsequence shim
     -> resolve Main Sequence workspace-analysis skill from configured package or SDK fallback
     -> resolve Main Sequence A2A communication skill from configured package or SDK fallback
  -> import interface/stream/server.ts
```

So the proposed architecture is not a rename. It requires moving real responsibilities out of the
current combined package.

## Current Runtime Identity Shape

The current implementation deploys the same Astro stream runtime in two Main Sequence-specific
identities:

```text
same Astro stream server/image/code
  -> astro-orchestrator
  -> project-executor
```

That is current behavior, not the desired Astro Core boundary.

`astro-orchestrator` currently means the Main Sequence control-plane agent runtime: user-facing
project creation, workspace analysis, A2A coordination, session orchestration, and project-executor
selection.

`project-executor` currently means a Main Sequence project-attached runtime selected by environment,
for example:

```text
ASTRO_FIXED_AGENT_TYPE=project-executor
ASTRO_FIXED_PROJECT_CWD=/workspace/project
```

Astro Core should not permanently own those Main Sequence names or policies. The target split is:

```text
Astro Core runtime
  generic Pi session runtime
  generic fixed-cwd/project-attached Pi runtime mechanics

Main Sequence deployment composition
  configures one Astro runtime as the Main Sequence orchestrator
  configures another Astro runtime as the Main Sequence project executor
  supplies the Main Sequence Pi package
  supplies the Main Sequence backend adapter
```

In other words, Astro Core may support generic runtime shapes such as "default session runtime" and
"fixed workspace cwd runtime", but `astro-orchestrator`, `project-executor`, and their product
policies belong to the Main Sequence package/adapter/deployment composition.

### Project-Executor Boundary

`project-executor` must not be treated as initial `@mainsequence/pi` package content.

In the current implementation, `project-executor` is a Main Sequence deployment composition made of:

- a backend-owned session identity, currently `AgentSession.uid` with `agent_type=project-executor`
- backend-owned model binding, checkpoint, provider credential, capability, and project attachment
  policy
- deployment env selecting the fixed runtime, currently `ASTRO_FIXED_AGENT_TYPE=project-executor`
  and `ASTRO_FIXED_PROJECT_CWD`
- a prepared project image/filesystem containing the selected project checkout, dependencies, and
  project-local runtime state
- project-local instructions, task/status files, and optional project-local Pi resources
- generic Astro Core mechanics for launching Pi in a fixed cwd, streaming output, preserving local
  state, and running a warm process

Those pieces are not the same kind of artifact as portable Pi skills/prompts/extensions. Therefore
the first external package simulation must stay orchestrator-focused and must not bundle a
standalone project-executor prompt, project setup workflow, checkpoint policy, fixed-cwd policy, or
backend session policy.

The only project-executor behavior that may eventually become portable Pi package content is a
small project-attached policy fragment or skill, and only after an inventory proves it is not already
provided by project-local resources, backend materialization, or the prepared project image.

The target ownership is:

```text
Astro Core
  generic fixed-cwd Pi runtime mechanics

Main Sequence Astro adapter / deployment composition
  project-executor identity, backend session authority, project attachment policy,
  checkpoint/credential/capability policy, and image/runtime wiring

Prepared project image/filesystem
  project source, dependencies, project-local instructions, task/status files,
  and project-local Pi resources

External Main Sequence Pi package
  orchestrator-facing portable Main Sequence skills/prompts/extensions first;
  project-executor package resources only after explicit inventory and acceptance
```

## Pi Terminology Used Here

This ADR follows Pi terminology and only names resource types this repository actually uses.

- A Pi extension is a TypeScript module loaded by Pi. In this repo, those live under
  `pi/extensions/`.
- A Pi skill is a `SKILL.md` resource loaded by Pi. In this repo, those live under `pi/skills/`.
- A Pi prompt is a prompt template loaded by Pi. In this repo, those live under `pi/prompts/`.
- A Pi package is the installable bundle that declares those resources in `package.json` under the
  `pi` key.

Therefore the portable Main Sequence artifact should be called a Pi package, not a Pi extension.
Only TypeScript modules inside that package are Pi extensions.

## Problem

Astro currently mixes four concerns:

1. General Pi deployment runtime behavior.
2. Backend session/checkpoint/credential/capability policy.
3. Main Sequence backend API details.
4. Main Sequence agent prompts, skills, extensions, and workflows.

That creates concrete problems:

- Generic runtime features look Main Sequence-specific even when they are not.
- Main Sequence backend failures can appear to be Astro Core failures.
- Public protocol work, especially A2A, is harder to reason about because runtime internals and
  backend session authority are interleaved.
- Startup and turn latency are hard to optimize by layer because bootstrap, credential hydration,
  capability materialization, checkpoints, and Pi launch are wired together.
- Main Sequence Pi behavior is not portable to plain Pi because it is bundled with Astro server and
  backend mechanics.
- A future local or third-party backend cannot reuse Astro without also inheriting Main Sequence
  assumptions.

## Decision

If Astro becomes a general Pi deployment runtime, the split should be:

```text
Astro Core
  Backend-neutral Pi deployment runtime.
  Owns HTTP/SSE/REST routes, public protocol translation, Pi launch, warm runners,
  local runtime state, local session files, stateless LLM passthrough, generic
  fixed-cwd/project-attached runtime mechanics, and adapter hooks.

External Main Sequence Pi Package
  Portable Main Sequence Pi behavior maintained outside Astro, preferably in
  the Main Sequence SDK repo or a sibling Main Sequence-owned Pi integration repo.
  Usable by plain Pi without Astro. Astro consumes this package; it does not own it.

Main Sequence Astro Adapter
  Optional Main Sequence backend/runtime integration for Astro.
  Owns backend sessions, checkpoints, provider credentials, capabilities, runtime
  credentials, Main Sequence runtime bootstrap, Main Sequence A2A/backend mapping,
  and Main Sequence runtime identity policy.
```

The package names are placeholders for the architecture boundary:

```text
@mainsequence/pi
@mainsequence/astro-adapter
```

This ADR does not claim those packages exist today. Today they are still folded into `astro`.

## Ownership Boundaries

### Astro Core

Astro Core should own backend-neutral runtime mechanics:

- HTTP server setup
- REST and SSE response shaping
- public A2A route handling and A2A object translation
- session-backed chat route handling
- stateless `POST /api/llm/chat`
- request parsing and validation
- Pi process launch
- Pi RPC warm-runner lifecycle
- warm-runner compatibility and invalidation mechanics
- local session file layout
- local runtime cwd and `.pi` materialization
- stream/event translation around Pi
- generic runtime profile mechanics, such as default session runtime versus fixed-cwd runtime
- model binding value objects
- provider/model catalog abstraction
- observability/logging primitives
- Docker/Kubernetes runtime shape that is not backend-specific

Astro Core must not construct Main Sequence API URLs or import Main Sequence SDK/auth helpers
directly once the adapter split is implemented. Astro Core must also not hardcode Main Sequence
runtime identity names such as `astro-orchestrator` or `project-executor` as its generic runtime
model.

### Main Sequence Deployment Composition

The Main Sequence deployment composition should own the concrete Main Sequence runtime identities
that currently live inside Astro:

```text
astro-orchestrator
project-executor
```

It should assemble:

- Astro Core runtime
- Main Sequence Astro adapter
- external Main Sequence Pi package
- deployment-specific environment and image wiring

This composition may deploy two Astro Core runtimes, but Astro Core should see them as generic
runtime shapes plus adapter-provided policy, not as built-in product roles.

### Backend Adapter Contract

The backend adapter should be capability-based, not one required mega-interface.

The proposed top-level adapter shape is:

```ts
type AstroBackendAdapter = {
  name: string;
  auth?: BackendAuthAdapter;
  sessions?: BackendSessionAdapter;
  checkpoints?: BackendCheckpointAdapter;
  providerCredentials?: BackendProviderCredentialAdapter;
  capabilities?: BackendCapabilityAdapter;
  modelCatalog?: BackendModelCatalogAdapter;
  projects?: BackendProjectAdapter;
  a2a?: BackendA2AAdapter;
  telemetry?: BackendTelemetryAdapter;
  bootstrap?: BackendRuntimeBootstrapAdapter;
};
```

Those names are not enough by themselves. If this ADR is accepted, Phase 1 must define the following
contracts before implementation starts.

#### Shared Adapter Values

The adapter boundary should pass normalized values, not raw Main Sequence API payloads, across Astro
Core:

```ts
type BackendUserRef = {
  uid: string;
};

type BackendAgentRef = {
  uid: string;
  agentType: string;
};

type BackendSessionRef = {
  uid: string;
  threadId: string | null;
  agent: BackendAgentRef;
  user: BackendUserRef | null;
  startedAt: string | null;
};

type BackendModelBinding = {
  provider: string;
  model: string;
  reasoningEffort: string | null;
  source: "request" | "session" | "agent" | "default";
};

type BackendProjectAttachment = {
  attached: boolean;
  projectId: string | null;
  cwd: string | null;
  repoRoot: string | null;
  projectImageRef: string | null;
};

type AdapterFailure = {
  code: string;
  status?: number;
  message: string;
  detail?: unknown;
  retryable?: boolean;
};

type AdapterResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: AdapterFailure };
```

The rule is simple: Astro Core can log raw backend details for debugging, but Core runtime decisions
should use these normalized values.

#### `auth`

The auth adapter owns backend authentication only. It must not own Pi provider auth or model
selection.

```ts
type BackendAuthAdapter = {
  resolveBackendHeaders(input: {
    userUid?: string | null;
    requestHeaders?: Record<string, string>;
    runtimeEnv: Record<string, string | undefined>;
  }): Promise<AdapterResult<Record<string, string>>>;
};
```

For Main Sequence, this is where runtime credential auth and backend API headers belong.

#### `sessions`

The sessions adapter owns backend session authority. Astro Core must not know Main Sequence session
endpoint paths.

```ts
type BackendSessionAdapter = {
  hydrateByUid(input: {
    uid: string;
    userUid?: string | null;
    requestSession?: unknown;
  }): Promise<AdapterResult<BackendSessionRef & {
    modelBinding: BackendModelBinding | null;
    projectAttachment: BackendProjectAttachment | null;
    raw?: unknown;
  }>>;
};
```

For Main Sequence, this wraps backend `AgentSession.uid` fetch/hydration and converts the response
into normalized session, model-binding, and project-attachment values.

#### `checkpoints`

The checkpoint adapter owns durable session state. Astro Core can ask for checkpoint operations, but
must not know the backend lease endpoints or lease payload shape.

```ts
type BackendCheckpointLease = {
  token: string;
  holderId: string;
  checkpointVersion: number | null;
  bundleHash: string | null;
};

type BackendCheckpointAdapter = {
  acquireLease(input: {
    sessionUid: string;
    reason: "start" | "turn" | "restore" | "finalize";
  }): Promise<AdapterResult<BackendCheckpointLease>>;

  renewLease(input: {
    sessionUid: string;
    lease: BackendCheckpointLease;
  }): Promise<AdapterResult<BackendCheckpointLease>>;

  restore(input: {
    sessionUid: string;
    lease: BackendCheckpointLease;
    targetDir: string;
  }): Promise<AdapterResult<{ restored: boolean; checkpointVersion: number | null }>>;

  finalize(input: {
    sessionUid: string;
    lease: BackendCheckpointLease;
    sourceDir: string;
    reason: "finish" | "cancel" | "error";
  }): Promise<AdapterResult<{ checkpointVersion: number | null; bundleHash: string | null }>>;

  release(input: {
    sessionUid: string;
    lease: BackendCheckpointLease;
    reason: "finish" | "cancel" | "error";
  }): Promise<AdapterResult<{ released: boolean }>>;

  cancel?(input: {
    sessionUid: string;
    reason: string;
  }): Promise<AdapterResult<{ cancelled: boolean }>>;
};
```

If a backend does not provide checkpoints, this adapter is absent and Astro Core must use local-only
state for endpoints that allow local-only execution.

#### `providerCredentials`

The provider credential adapter owns scoped model/provider credentials for Pi. It is distinct from
backend API authentication.

```ts
type BackendProviderCredentialAdapter = {
  hydrateScopedCredentials(input: {
    sessionUid: string;
    provider: string;
    targetAgentDir: string;
  }): Promise<AdapterResult<{
    scopedAgentDir: string;
    cacheHit: boolean;
    cacheReason: string | null;
  }>>;

  flushScopedCredentials?(input: {
    sessionUid: string;
    provider: string;
    sourceAgentDir: string;
  }): Promise<AdapterResult<{ flushed: boolean }>>;
};
```

For Main Sequence, this is the current scoped provider credential hydration/flush behavior.

#### `capabilities`

The capability adapter owns backend-provided session capabilities and backend-owned skill
materialization. It must be explicit because this step has real startup cost.

```ts
type BackendCapabilityAdapter = {
  materializeSessionCapabilities(input: {
    sessionUid: string;
    agentSessionUid: string;
    assetRoot: string;
    skillsRoot: string;
  }): Promise<AdapterResult<{
    skillPaths: string[];
    materializedSkillCount: number;
    cacheHit: boolean;
    cacheReason: string | null;
  }>>;
};
```

For Main Sequence, this wraps agent/session capability bindings and backend skill content lookup.

#### `modelCatalog`

The model catalog adapter owns backend/provider-specific model catalog policy. Astro Core should
carry normalized model bindings and pass them to Pi.

```ts
type BackendModelCatalogAdapter = {
  resolveModelBinding(input: {
    requestedProvider?: string | null;
    requestedModel?: string | null;
    requestedReasoningEffort?: string | null;
    session?: BackendSessionRef | null;
  }): Promise<AdapterResult<BackendModelBinding>>;
};
```

#### `projects`

The project adapter owns backend project/workspace attachment policy. Astro Core can prepare a cwd,
but it should not decide Main Sequence project identity.

```ts
type BackendProjectAdapter = {
  resolveProjectAttachment(input: {
    session: BackendSessionRef;
    requestedProjectId?: string | null;
    requestedCwd?: string | null;
  }): Promise<AdapterResult<BackendProjectAttachment>>;
};
```

#### `a2a`

The A2A adapter owns backend-specific A2A session/task mapping. Astro Core owns the public A2A HTTP
surface and Pi execution, but not Main Sequence backend identity rules.

```ts
type BackendA2AAdapter = {
  resolveMessageSend(input: {
    message: unknown;
    contextId?: string | null;
    taskId?: string | null;
    userUid?: string | null;
  }): Promise<AdapterResult<{
    session: BackendSessionRef;
    modelBinding: BackendModelBinding | null;
    projectAttachment: BackendProjectAttachment | null;
    runtimeMessage: string;
    responseFormat: unknown | null;
    metadata: Record<string, unknown>;
  }>>;

  normalizeTaskResponse?(input: {
    session: BackendSessionRef;
    piResult: unknown;
  }): Promise<AdapterResult<unknown>>;
};
```

For Main Sequence, this is where `message.contextId` maps to an authorized backend session uid.

#### `bootstrap`

The bootstrap adapter owns backend-specific runtime filesystem/bootstrap behavior. Astro Core owns
generic Pi filesystem layout, but the adapter owns Main Sequence runtime credential setup and
backend-provided skill installation.

```ts
type BackendRuntimeBootstrapAdapter = {
  prepareRuntime(input: {
    session: BackendSessionRef | null;
    cwd: string;
    agentDir: string;
    projectPiDir: string;
    runtimeEnv: Record<string, string | undefined>;
  }): Promise<AdapterResult<{
    env: Record<string, string>;
    settingsSkillPaths: string[];
    notes: string[];
  }>>;
};
```

For Main Sequence, this is where CLI config links, CLI shim setup, runtime credential exchange, and
runtime-owned library skill materialization belong.

Astro Core should ask capability questions instead of assuming Main Sequence:

```text
does this endpoint require durable sessions?
does this backend provide checkpoint leases?
does this backend provide provider credentials?
does this backend provide session capabilities?
does this backend provide project attachment?
does this backend provide A2A session/task mapping?
```

### Main Sequence Astro Adapter

The Main Sequence Astro adapter should own Main Sequence backend/runtime policy:

- Main Sequence runtime credential exchange
- auth headers for Main Sequence backend API requests
- backend `Agent.uid`
- backend `AgentSession.uid`
- backend session hydration
- session model binding hydration
- checkpoint lease acquire/renew/release/finalize
- checkpoint restore and flush
- runtime cancel requests
- provider credential status/hydrate/flush/revoke
- session-scoped provider credential directory setup
- agent capability and session capability binding fetches
- capability content fetches
- Main Sequence model catalog resolution
- project/workspace attachment from backend session metadata
- Main Sequence A2A message/task/session mapping
- Main Sequence-specific runtime bootstrap, including CLI config links, CLI shim, and
  backend-owned skill materialization
- Main Sequence-specific error normalization

The adapter should be the only layer that knows endpoint paths such as:

```text
/orm/api/agents/v1/sessions/{uid}/
/orm/api/agents/v1/sessions/{uid}/checkpoint_lease/acquire/
/orm/api/agents/v1/sessions/{uid}/checkpoint_lease/renew/
/orm/api/agents/v1/sessions/{uid}/capabilities/
/orm/api/agents/v1/model_provider_credentials/hydrate/
```

### External Main Sequence Pi Package

The Main Sequence Pi package should be external to Astro. It should be a Pi package, not an Astro
runtime package and not a backend adapter. It should contain only portable Pi resources that can run
when Pi is launched without Astro:

- SDK-owned Pi skills
- SDK-owned Pi prompts
- portable TypeScript extensions only when Main Sequence has actual Pi-harness behavior that does
  not require Astro Core imports, Astro session files, or backend authority
- supporting reference files used by those SDK-owned skills/prompts
- package metadata using Pi's existing `package.json` `pi` field

Astro must not be the source of truth for this package. The source of truth should be one of:

- the Main Sequence SDK repository, for example under `agent_scaffold/skills/**` and
  `agent_scaffold/prompts/**`
- a sibling Main Sequence-owned repository dedicated to Pi integration

The external package may be published as `@mainsequence/pi`, but its content must be maintained
outside the Astro repository.

The package must not contain:

- Astro HTTP/SSE/REST server code
- checkpoint clients, lease renewal, restore, flush, or cancel code
- provider credential hydration/flush/revoke code
- backend session hydration or `AgentSession.uid` lookup code
- Main Sequence runtime credential exchange implementation
- runtime-owned CLI auth repair that imports Astro host runtime code
- deployment/runtime-profile wiring for Main Sequence identities such as `astro-orchestrator` and
  `project-executor`
- A2A request routing, task mapping, or backend session serialization rules
- Kubernetes/Docker deployment wiring

The package can still document and use the normal Main Sequence CLI and SDK. It may also expose
portable Pi tools such as a CLI-auth repair tool, provided the host supplies the repair mechanism
and the package does not import Astro host runtime code. The boundary is that normal CLI/SDK usage is
portable; Astro-managed auth/session/checkpoint behavior is not.

#### SDK-Owned Package Contents

The independent Pi package should delegate project/component knowledge to the SDK-owned project
builder skill rather than copying component maps into Astro:

```text
https://github.com/mainsequence-sdk/mainsequence-sdk/tree/main/agent_scaffold/skills/project_builder
```

Main Sequence project creation, SDK explanation, workspace/product guidance, and any other portable
Pi skills should live with that SDK-owned package source. Astro may consume them; Astro should not
copy or fork them as package contents.

#### Current Astro Resources

Root `pi/` and `.pi/` are now treated as Astro Core Pi resources. Main Sequence-facing Pi resources
are represented by the local package simulation, not by root `pi/`.

Resources that have moved into the local package simulation and should later be reconciled with the
external package source of truth:

```text
tmp_ms_pi/pi/prompts/review-main-sequence-project.md
tmp_ms_pi/pi/prompts/verify-mainsequence-tutorial.md
tmp_ms_pi/pi/extensions/hooks/scaffold-skill-discovery/
tmp_ms_pi/pi/extensions/hooks/project-policy/
tmp_ms_pi/pi/extensions/tools/mainsequence-cli-auth/
tmp_ms_pi/pi/extensions/tools/mainsequence-runtime-info/
tmp_ms_pi/pi/system/APPEND_SYSTEM.md
```

The package simulation does not directly declare `pi/skills`. SDK-owned skills remain source-of-truth
SDK resources. The local package simulation seeds them through a Pi `resources_discover` extension
that calls the SDK-owned copy/export command when the runtime cwd does not already contain
`.agents/skills`.

The following resources were split by concern during the local simulation:

```text
pi/extensions/tools/runtime-info/                  Astro runtime details only
tmp_ms_pi/pi/extensions/tools/mainsequence-runtime-info/
.pi/APPEND_SYSTEM.md                              Astro Core runtime contract only
tmp_ms_pi/pi/system/APPEND_SYSTEM.md              Main Sequence runtime contract
```

`runtime-info` should stay Astro-only. Main Sequence SDK/CLI version tooling belongs to the package
simulation or the eventual external package.

Astro Core/runtime-owned resources must stay out of the external package:

```text
pi/extensions/hooks/session-model/
pi/extensions/hooks/telemetry/
pi/extensions/shared/structured-logging.ts
pi/extensions/shared/telemetry.ts
interface/stream/a2a-runtime.ts
interface/stream/mainsequence-agent-registration.ts
adapters/mainsequence/runtime-auth.ts
runtime/checkpoints/sidecar.ts
interface/stream/session-checkpoint-client.ts
interface/stream/model-provider-credentials-client.ts
interface/stream/session-capabilities.ts
```

`interface/stream/mainsequence-agent-registration.ts` and `adapters/mainsequence/runtime-auth.ts`
are not Astro Core in the target architecture either. They belong to the Main Sequence
adapter/composition, not to the portable Pi package.

Current runtime-injected library skills such as `command_center/workspace_analysis` and
`a2a_communication` are not Astro-owned package contents. Bootstrap should stop hard-coding those
slugs. The Main Sequence Pi package should expose a `resources_discover` hook that delegates skill
copying/export to the SDK/CLI, so Astro TypeScript does not duplicate `agent_scaffold` copy logic.
SDK-owned skills such as `project_builder` are source-of-truth SDK resources, not component maps
Astro should copy.

When the external package is introduced, Astro must account for the existing delivery paths. For
each SDK-owned skill or prompt being tested, record whether it comes from:

```text
runtime bootstrap/session/image materialization
external @mainsequence/pi package loading
```

This is a cutover concern, not a new public contract.

## Target Package Layout

The target layout should mirror current Pi resource types and avoid invented resource categories:

```text
external @mainsequence/pi package
  Lives outside Astro, preferably in the Main Sequence SDK repo or a sibling
  Main Sequence-owned repo.
  Uses Pi package metadata to expose skills, prompts, and optional portable
  extensions.

packages/mainsequence-astro-adapter/
  package.json
  src/
    adapter.ts
    auth.ts
    sessions.ts
    checkpoints.ts
    provider-credentials.ts
    capabilities.ts
    model-catalog.ts
    projects.ts
    a2a.ts
    runtime-bootstrap.ts
    container.ts
  pi/
    extensions/
      hooks/
      tools/
```

The adapter may include Pi extensions only for behavior that must run inside the Pi harness but is
not portable without the Astro/Main Sequence backend. Those adapter-owned extensions must be kept
separate from the external portable Main Sequence Pi package.

The external portable package contract should use Pi's existing package metadata:

```json
{
  "name": "@mainsequence/pi",
  "keywords": ["pi-package"],
  "type": "module",
  "pi": {
    "extensions": [
      "./pi/extensions/hooks",
      "./pi/extensions/tools"
    ],
    "prompts": [
      "./pi/prompts"
    ],
    "skills": [
      "./pi/skills"
    ]
  }
}
```

## Endpoint Consequences

This ADR should not change public endpoints by itself.

Current endpoint ownership after the split should be:

```text
POST /api/llm/chat
  Astro Core.
  Stateless provider call.
  No Pi runner, backend session, checkpoint, capability materialization, or persistence.
  May call backend adapter only for provider credential policy if configured.

POST /api/a2a/v1/message:send
  Astro Core owns the A2A public protocol boundary.
  Backend adapter owns authorized session/task mapping and backend session authority.
  Astro Core owns local Pi/warm-runner execution after adapter policy resolves.

POST /api/chat
  Astro Core owns stream handling and Pi execution.
  Backend adapter owns backend session/checkpoint/credential/capability/project policy when enabled.

GET /api/chat/session-model
PATCH /api/chat/session-config
  Astro Core owns request/response shape.
  Backend adapter owns backend-backed mutation or hydration when enabled.
```

This ADR explicitly excludes a return to public runtime attachment endpoints. The attach-like
concept is now internal execution state: prepared runtime, cached preflight, and warm runner.

## Migration Plan If Accepted

### Phase 1: Define Interfaces Without Moving Behavior

Phase 1 is a definition phase, not an extraction phase. It must produce the exact boundary documents
and implementation tasks before package creation or file movement.

Definition deliverables:

- Keep this ADR as the source of truth for the proposed package/adapter split.
- Use [Integrate External `@mainsequence/pi` Package](./mainsequence-pi-external-package-integration-task.md)
  for the package integration task.
- Add or update future implementation task documents for extraction, adapter creation, and runtime
  cutover work.
- Do not create, publish, or switch to a production package scaffold inside Astro. The temporary
  `tmp_ms_pi` simulation is allowed only for local contract testing.
- Root `pi/` should contain only Astro runtime-owned Pi resources during the local simulation.
- Do not hardcode `tmp_ms_pi` into Astro Core. Main Sequence deployment configuration supplies it
  through `ASTRO_PI_PACKAGE_PATHS`.

External Pi package integration definition tasks:

- Treat the temporary `tmp_ms_pi` simulation as orchestrator-focused only until project-executor has
  its own inventory and ownership decision.
- Inventory existing orchestrator skill/prompt/extension delivery paths before deciding what
  `@mainsequence/pi` should expose to Astro.
- Include at least these delivery paths in the inventory:
  - `ASTRO_PI_PACKAGE_PATHS` package loading for `tmp_ms_pi`
  - `tmp_ms_pi/pi/extensions/hooks/scaffold-skill-discovery` SDK skill seeding
  - session capability materialization
- For project-executor, create a separate inventory that classifies each behavior as one of:
  - generic Astro Core fixed-cwd runtime mechanic
  - Main Sequence adapter/deployment composition
  - backend-owned session/project/checkpoint/credential/capability policy
  - prepared project image/filesystem content
  - project-local instruction/task/status/Pi resource
  - future portable Pi package content
- Do not add project-executor content to `tmp_ms_pi` unless that separate inventory proves the
  behavior is portable Pi package content and not already supplied by the prepared project image or
  project-local resources.
- Classify every current `pi/skills/*` resource as one of:
  - portable Main Sequence package content
  - Astro/runtime content
  - repository-maintenance content
  - split-required content
- Classify every current `pi/prompts/*` resource as one of:
  - portable Main Sequence package content
  - Astro/runtime content
  - split-required content
- Classify every current `pi/extensions/**` resource as one of:
  - portable Main Sequence package extension
  - Astro Core extension
  - Main Sequence Astro adapter extension
  - shared helper for one of those buckets
  - repo-local TypeScript support
- For every split-required file, list the exact portable text/behavior and the exact runtime-owned
  text/behavior.
- Define the expected external `@mainsequence/pi` manifest only as an external package contract, not
  as an Astro-owned package file.
- Do not add `packages/mainsequence-pi` to Astro. Astro should consume an external package source
  after the temporary `tmp_ms_pi` simulation proves the package contract.
- For every SDK-owned skill/prompt being tested, record the active Astro runtime delivery path:
  - existing bootstrap/session/image materialization
  - external `@mainsequence/pi` package loading
- Use the temporary `tmp_ms_pi` package to make that delivery visible before any runtime cutover.
- Define acceptance criteria for the future package extraction:
  - Pi package resolver loads the extracted package.
  - Portable skills/prompts do not reference `ASTRO_*` variables.
  - Portable package does not import `interface/stream/*` or `adapters/mainsequence/runtime-auth.ts`.
  - Portable package can be loaded by Pi without Astro runtime bootstrap.

Backend adapter definition tasks:

- Treat the contracts in the "Backend Adapter Contract" section as the Phase 1 baseline.
- Define normalized shared values: `BackendUserRef`, `BackendAgentRef`, `BackendSessionRef`,
  `BackendModelBinding`, `BackendProjectAttachment`, `AdapterFailure`, and `AdapterResult`.
- Define each optional adapter capability:
  - `auth`
  - `sessions`
  - `checkpoints`
  - `providerCredentials`
  - `capabilities`
  - `modelCatalog`
  - `projects`
  - `a2a`
  - `bootstrap`
- For each existing Main Sequence client/module, map it to exactly one adapter capability or mark it
  as Astro Core:
  - `adapters/mainsequence/runtime-auth.ts`
  - `interface/stream/mainsequence-agent-registration.ts`
  - `interface/stream/session-checkpoint-client.ts`
  - `interface/stream/model-provider-credentials-client.ts`
  - `interface/stream/session-capabilities.ts`
  - runtime-owned skill materialization in bootstrap code
- Define which endpoints require which adapter capabilities:
  - `POST /api/llm/chat`
  - `POST /api/chat`
  - `POST /api/a2a/v1/message:send`
  - `GET /api/chat/session-model`
  - `PATCH /api/chat/session-config`
- Define behavior when an adapter capability is absent:
  - reject the endpoint with a clear configuration error
  - use local-only behavior
  - skip the optional operation

Implementation tasks after definitions are accepted:

- Add a Main Sequence adapter wrapper around current Main Sequence clients without changing public
  endpoint behavior.
- Add a null/local adapter for endpoints that do not need backend policy.
- Keep public endpoint behavior unchanged.
- Do not introduce new public attach endpoints.

### Phase 2: Move Main Sequence API Clients Behind Adapter

- Move backend session hydration behind `adapter.sessions`.
- Move checkpoint client behind `adapter.checkpoints`.
- Move provider credential client behind `adapter.providerCredentials`.
- Move capability client behind `adapter.capabilities`.
- Move Main Sequence auth header resolution behind `adapter.auth`.
- Move Main Sequence A2A backend mapping behind `adapter.a2a`.

### Phase 3: Split Runtime Bootstrap

- Keep generic Pi filesystem/bootstrap in Astro Core.
- Move Main Sequence CLI auth, CLI shim, runtime credential exchange, and backend-owned skill
  materialization into the Main Sequence Astro adapter.
- Make Astro Core startup work without the `mainsequence` Python package when no Main Sequence
  adapter is enabled.

### Phase 4: Consume External Main Sequence Pi Package

- Move or define portable Main Sequence skills and prompts in the external package source, not in
  Astro.
- Move only portable TypeScript extensions into the external package source if such extensions are
  actually needed.
- Keep Astro/runtime-managed hooks and tools in Astro Core or `@mainsequence/astro-adapter`.
- Update Astro deployment configuration to consume the external package.

### Phase 5: Split Images And Deployment Configuration

- Build a backend-neutral Astro runtime image.
- Build a Main Sequence image variant that installs Astro Core, `@mainsequence/astro-adapter`, and
  the external `@mainsequence/pi` package.
- Make checkpoint sidecar selection backend-policy driven.
- Document final package selectors only after the package/adaptor mechanism exists.

## Compatibility Expectations

The refactor must preserve current Main Sequence behavior during migration:

- Existing backend-backed sessions continue to use `AgentSession.uid`.
- Existing A2A `message:send` behavior continues.
- Existing stateless `/api/llm/chat` behavior continues.
- Existing checkpoint, provider credential, and capability behavior continues through the adapter.
- Existing Main Sequence runtime identities remain `astro-orchestrator` and `project-executor`
  during migration, but they are preserved by the Main Sequence composition/adapter rather than
  becoming permanent Astro Core concepts.

Compatibility wrappers are acceptable during extraction. Behavior changes should be separate ADRs or
implementation tasks.

## Non-Goals

This ADR does not:

- remove Main Sequence support
- remove backend-backed sessions
- remove checkpointing
- remove provider credential hydration
- remove capabilities or skills
- replace Pi core
- define a new A2A protocol
- revive public runtime attachment endpoints
- decide final package names
- decide final CLI flags or environment variable names
- require immediate code changes

## Risks

- The split may create unnecessary abstraction if Astro remains permanently Main Sequence-only.
- A partial split could make the code harder to follow if Main Sequence logic is moved halfway.
- Some current Pi extensions are mixed: they are TypeScript extensions, but their behavior depends
  on Astro runtime/backend authority.
- Startup behavior may become harder to debug if bootstrap responsibility is split without clear
  logs.
- Local and third-party adapter modes would need product support, documentation, and tests.

## Benefits

- Astro Core becomes understandable as a Pi deployment runtime.
- Main Sequence backend policy becomes explicit instead of scattered through stream handling.
- Main Sequence Pi behavior becomes portable to plain Pi where it is actually portable.
- Public A2A remains standard and does not leak runtime attachment mechanics.
- Stateless LLM calls remain fast and separate from session-backed agent execution.
- Startup and turn latency can be analyzed by layer: Astro Core, backend adapter, Pi package, or Pi
  process.

## Decision Required

Before implementation, the project must decide whether to keep Astro as a Main Sequence-specific
runtime package or extract the boundary described here:

```text
astro
  backend-neutral Pi deployment runtime

@mainsequence/pi
  external portable Main Sequence Pi package owned outside Astro

@mainsequence/astro-adapter
  Main Sequence backend/runtime adapter for Astro
```

If the extraction is accepted, this ADR becomes the boundary for the refactor. If not, the better
path is cleanup inside the current Main Sequence-specific `astro` package.
