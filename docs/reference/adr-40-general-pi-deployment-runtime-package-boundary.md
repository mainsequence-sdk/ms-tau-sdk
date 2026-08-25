# ADR 40: General Pi Deployment Runtime Adapter Boundary

Status: Proposed
Date: 2026-06-19
Implementation Status: Partially implemented. ADR 39 is implemented, the Main Sequence
adapter-owned Pi resource overlay now lives under `adapters/mainsequence/pi-overlay`, Astro can consume
additional Pi package resource roots through `ASTRO_PI_PACKAGE_PATHS`, root `pi/` is treated as
Astro Core Pi resources, and Main Sequence SDK skill discovery is delegated through the Main
Sequence overlay. The generic backend adapter contract exists, `ASTRO_BACKEND=mainsequence` is the
only supported implementation, and auth, identity, sessions, checkpoints, provider credentials,
session capabilities, and model catalog access now enter through that adapter. Main Sequence
CLI/config/shim bootstrap now enters through `adapter.bootstrap`. The Dockerfile now has a
backend-neutral `astro-core` build stage and explicit `astro-mainsequence-*` deployment targets;
current Compose and GCP deployment still intentionally select the Main Sequence target. The
previous standalone external package cutover direction is superseded. Project attachment, A2A
backend policy, a runnable null/custom backend, backend-neutral startup, and adapter-driven
checkpoint sidecar behavior are still not complete.

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

Main Sequence Pi product resources are currently represented by the adapter-owned
`adapters/mainsequence/pi-overlay` overlay. That overlay is adapter/deployment-specific. It is not a
standalone production package boundary.

The proposed direction is to split those responsibilities along Astro Core and backend-adapter
boundaries. SDK-owned skills and prompts stay source-of-truth in the Main Sequence SDK/CLI and are
discovered by the Main Sequence adapter resource overlay.

## Implementation Task Documents

This ADR is an architecture proposal. Concrete implementation work may be tracked in separate task
documents, but the standalone external package cutover task is no longer the active target for this
ADR. The active target is the Main Sequence adapter-owned Pi resource overlay while SDK skills and
prompts remain owned by the Main Sequence SDK/CLI.

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
- Agent-targeted sessionless execution uses `POST /api/agents/{agent_uid}/responses` and must not
  go through sessions, checkpoints, Pi runners, project attachment, queues, or persistence. ADR 44
  supersedes the former unscoped model passthrough.
- Main Sequence runtime credential auth remains the deployed-runtime auth mechanism.
- Main Sequence session capabilities and injected skills remain current behavior until an adapter
  split replaces the direct imports.

## Current Public Runtime Surface

Current server startup advertises these primary endpoints:

```text
POST /api/agents/{agent_uid}/responses
POST /api/agents/{agent_uid}/responses/stream
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

No standalone production Main Sequence Pi package is required by this ADR. Main Sequence
adapter-owned Pi resources live under `adapters/mainsequence/pi-overlay`, and that path is the adapter
overlay loaded by Main Sequence Astro images.

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

The Main Sequence adapter Pi resource overlay currently carries the Main Sequence product resources:

```text
adapters/mainsequence/pi-overlay/pi/extensions/hooks/project-policy/index.ts
adapters/mainsequence/pi-overlay/pi/extensions/hooks/scaffold-skill-discovery/index.ts
adapters/mainsequence/pi-overlay/pi/extensions/tools/mainsequence-cli-auth/index.ts
adapters/mainsequence/pi-overlay/pi/extensions/tools/mainsequence-runtime-info/index.ts
adapters/mainsequence/pi-overlay/pi/prompts/review-main-sequence-project.md
adapters/mainsequence/pi-overlay/pi/prompts/verify-mainsequence-tutorial.md
adapters/mainsequence/pi-overlay/pi/system/APPEND_SYSTEM.md
```

Current server/runtime code no longer imports the first adapter slices directly from
Main Sequence/backend modules. The stream server resolves one backend adapter and requires the
capabilities it needs:

```text
interface/stream/server.ts
  -> resolveBackendAdapter(process.env)
  -> require auth, identity, sessions, checkpoints, providerCredentials, capabilities, modelCatalog

adapters/mainsequence/adapter.ts
  wraps Main Sequence auth/session/checkpoint/provider credential/capability/model modules
```

Current bootstrap is partially split. Astro Core prepares the generic runtime filesystem, then calls
`backendAdapter.bootstrap` for backend-specific setup:

```text
bin/astro-stream.ts
  -> bootstrapPiAgentDir()
     -> materialize runtime .pi settings and cwd
     -> compose configured Pi package settings and system prompts
  -> backendAdapter.bootstrap?.prepareRuntime()
     -> Main Sequence adapter creates CLI config link
     -> Main Sequence adapter creates managed mainsequence shim
     -> Main Sequence adapter wires CLI auth repair command
  -> import interface/stream/server.ts
```

So the proposed architecture is not a rename. It requires moving real responsibilities out of Astro
Core and into the Main Sequence adapter/deployment composition.

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
  supplies the Main Sequence adapter-owned Pi resource overlay
  supplies the Main Sequence backend adapter
```

In other words, Astro Core may support generic runtime shapes such as "default session runtime" and
"fixed workspace cwd runtime", but `astro-orchestrator`, `project-executor`, and their product
policies belong to the Main Sequence adapter/deployment composition.

### Project-Executor Boundary

`project-executor` must not be treated as standalone portable Pi package content.

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
the Main Sequence Pi resource overlay must not bundle a standalone project-executor prompt, project
setup workflow, checkpoint policy, fixed-cwd policy, or backend session policy.

The only project-executor behavior that may eventually become Pi resource overlay content is a small
project-attached policy fragment or skill, and only after an inventory proves it is not already
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

Main Sequence adapter Pi resource overlay
  Main Sequence runtime-facing prompts/extensions/tools that depend on this deployment composition;
  project-executor overlay resources only after explicit inventory and acceptance
```

## Pi Terminology Used Here

This ADR follows Pi terminology and only names resource types this repository actually uses.

- A Pi extension is a TypeScript module loaded by Pi. In this repo, those live under
  `pi/extensions/`.
- A Pi skill is a `SKILL.md` resource loaded by Pi. In this repo, those live under `pi/skills/`.
- A Pi prompt is a prompt template loaded by Pi. In this repo, those live under `pi/prompts/`.
- A Pi package is the installable bundle that declares those resources in `package.json` under the
  `pi` key.

Therefore any Main Sequence resource overlay loaded through Pi package mechanics must still use Pi
terminology correctly: TypeScript modules are extensions, Markdown prompt files are prompts, and SDK
`SKILL.md` resources are skills. This ADR does not require those resources to be published as a
separate external package.

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
  local runtime state, local session files, agent-targeted sessionless responses, generic
  fixed-cwd/project-attached runtime mechanics, and adapter hooks.

Main Sequence Astro Adapter
  Optional Main Sequence backend/runtime integration for Astro.
  Owns backend sessions, checkpoints, provider credentials, capabilities, runtime
  credentials, Main Sequence runtime bootstrap, Main Sequence A2A/backend mapping,
  Main Sequence runtime identity policy, and the Main Sequence Pi resource overlay loaded by Astro.
```

The adapter package name is a placeholder for the architecture boundary:

```text
@mainsequence/astro-adapter
```

This ADR does not claim that package exists today. Today the adapter code and resource overlay are
still folded into `astro`.

## Ownership Boundaries

### Astro Core

Astro Core should own backend-neutral runtime mechanics:

- HTTP server setup
- REST and SSE response shaping
- public A2A route handling and A2A object translation
- session-backed chat route handling
- sessionless `POST /api/agents/{agent_uid}/responses` and `/responses/stream`
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
- Main Sequence adapter Pi resource overlay
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

### Main Sequence Adapter Pi Resource Overlay

The current `adapters/mainsequence/pi-overlay` contents are intentionally not a standalone package
boundary. They are a Main Sequence Astro runtime overlay:

- `scaffold-skill-discovery` calls the installed `mainsequence` SDK/CLI to discover SDK-owned
  skills.
- `mainsequence-cli-auth` depends on Astro-managed Main Sequence runtime auth repair.
- `mainsequence-runtime-info` reports runtime/deployment information.
- `project-policy` is tied to this deployed Main Sequence Astro agent behavior.
- `adapters/mainsequence/pi-overlay/pi/system/APPEND_SYSTEM.md` describes the Main Sequence Astro agent contract, not a
  generic plain-Pi package contract.

Therefore the target is not a standalone external package cutover. The target is:

```text
Astro Core
  root .pi/ and pi/ resources that are backend-neutral

Main Sequence Astro adapter
  Main Sequence backend/runtime adapter code
  Main Sequence-specific Pi resource overlay
  SDK skill/prompt discovery through the installed Main Sequence SDK/CLI

Main Sequence SDK/CLI
  source of truth for SDK-owned skills and prompts
```

The adapter-owned overlay may still be loaded through Pi package mechanics because that is how Pi
discovers extensions and prompts. That does not make it a separate product package. It is part of
the Main Sequence Astro adapter/deployment composition.

#### SDK-Owned Resources

SDK-owned skills and prompts must stay source-of-truth in the Main Sequence SDK/CLI, for example:

```text
agent_scaffold/skills/**
agent_scaffold/prompts/**
```

Main Sequence project creation, SDK explanation, workspace/product guidance, and any other SDK-owned
skill should be discovered or copied from the installed SDK/CLI. Astro must not copy component maps
or fork SDK skill bodies into this repository.

The adapter overlay can include a discovery extension that delegates to the SDK/CLI. That extension
is adapter-owned because it depends on the Main Sequence runtime composition, but the skill content
it exposes is SDK-owned.

#### Current Astro Resources

Root `pi/` and `.pi/` are Astro Core Pi resources:

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

Current Main Sequence adapter overlay resources live in `adapters/mainsequence/pi-overlay`:

```text
adapters/mainsequence/pi-overlay/pi/prompts/review-main-sequence-project.md
adapters/mainsequence/pi-overlay/pi/prompts/verify-mainsequence-tutorial.md
adapters/mainsequence/pi-overlay/pi/extensions/hooks/scaffold-skill-discovery/
adapters/mainsequence/pi-overlay/pi/extensions/hooks/project-policy/
adapters/mainsequence/pi-overlay/pi/extensions/tools/mainsequence-cli-auth/
adapters/mainsequence/pi-overlay/pi/extensions/tools/mainsequence-runtime-info/
adapters/mainsequence/pi-overlay/pi/system/APPEND_SYSTEM.md
```

This is the target adapter-owned location. The ownership rule is the important boundary: these
resources belong with the Main Sequence Astro adapter, not with Astro Core and not with an external
plain-Pi package.

The following split remains valid:

```text
pi/extensions/tools/runtime-info/                  Astro runtime details only
adapters/mainsequence/pi-overlay/pi/extensions/tools/mainsequence-runtime-info/
.pi/APPEND_SYSTEM.md                              Astro Core runtime contract only
adapters/mainsequence/pi-overlay/pi/system/APPEND_SYSTEM.md              Main Sequence runtime contract
```

Astro Core/runtime-owned resources must stay out of the Main Sequence overlay:

```text
pi/extensions/hooks/session-model/
pi/extensions/hooks/telemetry/
pi/extensions/shared/structured-logging.ts
pi/extensions/shared/telemetry.ts
interface/stream/a2a-runtime.ts
runtime/checkpoints/sidecar.ts
interface/stream/session-checkpoint-client.ts
interface/stream/model-provider-credentials-client.ts
interface/stream/session-capabilities.ts
```

Main Sequence backend-specific modules are not Astro Core either. They belong to
`adapters/mainsequence`, not to the Pi overlay:

```text
interface/stream/mainsequence-agent-registration.ts
adapters/mainsequence/runtime-auth.ts
```

#### Target Layout

The target layout should mirror current Pi resource types and avoid invented resource categories:

```text
adapters/mainsequence/
  adapter.ts
  auth.ts
  sessions.ts
  checkpoints.ts
  provider-credentials.ts
  capabilities.ts
  model-catalog.ts
  projects.ts
  a2a.ts
  bootstrap.ts
  pi/
    package.json
    README.md
    pi/
      extensions/
        hooks/
        tools/
      prompts/
      system/
```

The Main Sequence image/deployment composition should then point `ASTRO_PI_PACKAGE_PATHS` at the
adapter-owned Pi overlay path until a better in-process package registration mechanism exists.

## Endpoint Consequences

This ADR should not change public endpoints by itself.

Current endpoint ownership after the split should be:

```text
POST /api/agents/{agent_uid}/responses
POST /api/agents/{agent_uid}/responses/stream
  Astro Core.
  Sessionless AgentHarness call using a deployment-provided agent snapshot.
  No Pi runner, backend session, checkpoint, capability materialization, or persistence.
  May call the backend adapter only for provider credential hydration or refresh.

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

## Implementation Tasks

This checklist is the implementation plan for ADR 40. Completed items describe current repository
state. Unchecked items are the remaining work.

### Completed Foundation

- [x] Implement ADR 39 so `astro-orchestrator` and `project-executor` are backend/session metadata,
  not separate Astro Core runtime architectures.
- [x] Keep this ADR as the source of truth for the proposed Astro Core / backend adapter /
  adapter-owned Pi resource overlay split.
- [x] Create the Main Sequence adapter-owned Pi resource overlay under `adapters/mainsequence/pi-overlay`.
- [x] Keep `adapters/mainsequence/pi-overlay` private and adapter-owned; do not treat it as an external
  production package source of truth.
- [x] Wire Astro deployments to consume additional Pi packages through `ASTRO_PI_PACKAGE_PATHS`.
- [x] Include `ASTRO_PI_PACKAGE_PATHS=/app/adapters/mainsequence/pi-overlay` in Astro-owned no-project and
  project-attached Docker image/env examples.
- [x] Keep root `pi/` limited to Astro Core Pi resources during the current overlay staging period.
- [x] Move Main Sequence-facing prompts, system prompt content, and Pi tools/hooks out of root `pi/`
  and into `adapters/mainsequence/pi-overlay`.
- [x] Split `runtime-info` by concern: Astro runtime information stays in root `pi/`, while Main
  Sequence SDK/CLI runtime information lives in `adapters/mainsequence/pi-overlay`.
- [x] Remove hardcoded SDK skill-slug materialization from Astro bootstrap.
- [x] Delegate SDK-owned skill discovery/copying to
  `adapters/mainsequence/pi-overlay/pi/extensions/hooks/scaffold-skill-discovery`.
- [x] Keep SDK-owned skills source-of-truth in the Main Sequence SDK/CLI. The overlay calls
  `mainsequence skills path` instead of copying component maps or skill bodies into Astro.
- [x] Do not add standalone `project-executor` package content to `adapters/mainsequence/pi-overlay`.
- [x] Preserve public endpoints. This ADR does not add public runtime attachment endpoints.
- [x] Keep public A2A on `POST /api/a2a/v1/message:send` and related standard A2A routes.
- [x] Replace the unscoped model route with ADR 44 agent-targeted sessionless response endpoints,
  separate from Pi/session/checkpoint execution.
- [x] Supersede the standalone external package cutover direction. The active target is a
  Main Sequence adapter-owned Pi resource overlay plus SDK/CLI-owned skill content.

### Next Step: Generic Backend Adapter Contract

The next step is not a Main Sequence-specific interface. Astro Core must first define the generic
backend adapter contract. Main Sequence is then the first implementation of that contract.

- [x] Add a backend adapter contract module, for example `adapters/types.ts` or
  `runtime/backend-adapter.ts`.
- [x] Define normalized shared values that cross the Astro Core/adapter boundary:
  `BackendUserRef`, `BackendAgentRef`, `BackendSessionRef`, `BackendModelBinding`,
  `BackendProjectAttachment`, `BackendAuthHeaders`, `AdapterFailure`, and `AdapterResult`.
- [x] Define optional adapter capabilities:
  `auth`, `sessions`, `checkpoints`, `providerCredentials`, `capabilities`, `modelCatalog`,
  `projects`, `a2a`, and `bootstrap`.
- [x] Document which capabilities each endpoint requires and which capabilities are optional.
- [x] Define behavior when a configured adapter lacks a capability:
  local-only fallback, optional skip, or clear unsupported-backend error.
- [x] Add adapter resolution/selection with `ASTRO_BACKEND`, defaulting to `mainsequence` for
  current deployment compatibility.
- [x] Reject unknown `ASTRO_BACKEND` values with a clear startup/configuration error.
- [x] Add adapter capability checks so endpoints can fail clearly when a required backend capability
  is absent.

#### Endpoint Capability Matrix

This matrix describes the adapter capabilities each current endpoint should require after the
adapter split. "Required" means the endpoint cannot perform its intended backend-aware behavior
without that adapter capability. "Optional" means Astro Core can still answer the request, but may
omit backend-enriched data or skip backend-specific behavior. "Current status" records whether the
capability is already called through `backendAdapter` or still uses a direct module pending a later
adapter slice.

| Endpoint | Required adapter capabilities | Optional adapter capabilities | Current status |
| --- | --- | --- | --- |
| `OPTIONS *` | none | none | Core-only CORS preflight. |
| `GET /health` | none | none | Core-only runtime health snapshot. |
| `POST /api/agents/{agent_uid}/responses[/stream]` | `providerCredentials` only | none | Agent-targeted sessionless fast path. Agent defaults, instructions, capability eligibility, and media policy come from the immutable local deployment snapshot. It does not require sessions, checkpoints, projects, or durable A2A persistence. |
| `GET /api/chat/get_available_models` | none for local/Pi registry models | `modelCatalog`, `providerCredentials` for backend-authenticated provider status | Server now calls `backendAdapter.modelCatalog`; the Main Sequence adapter wraps the existing model/provider modules. |
| `GET /api/models/catalog` | none for local/Pi registry catalog | `modelCatalog`, `providerCredentials` for backend-authenticated provider status | Server now calls `backendAdapter.modelCatalog`; the Main Sequence adapter wraps the existing model/provider modules. |
| `GET /api/model-providers` | `providerCredentials` | `modelCatalog` for known model counts | Server now calls `backendAdapter.providerCredentials`. |
| `POST /api/model-providers/{provider}/signin` | `providerCredentials` | none | Server now calls `backendAdapter.providerCredentials`. |
| `POST /api/model-providers/{provider}/signoff` | `providerCredentials` | none | Server now calls `backendAdapter.providerCredentials`. |
| `GET /api/model-providers/{provider}/signin/{attemptId}` | `providerCredentials` | none | Server now calls `backendAdapter.providerCredentials`; local attempt state remains implementation detail behind the Main Sequence adapter wrapper. |
| `POST /api/model-providers/{provider}/signin/{attemptId}/manual` | `providerCredentials` | none | Server now calls `backendAdapter.providerCredentials`; local attempt state remains implementation detail behind the Main Sequence adapter wrapper. |
| `POST /api/model-providers/{provider}/signin/{attemptId}/cancel` | `providerCredentials` | none | Server now calls `backendAdapter.providerCredentials`; local attempt state remains implementation detail behind the Main Sequence adapter wrapper. |
| `GET /api/chat/session-model` | `sessions` when local session metadata is missing and backend hydration is needed | `modelCatalog` for future backend-owned model metadata validation | `sessions` is already behind `backendAdapter.sessions`. Model metadata repair remains Core/session-file logic. |
| `PATCH /api/chat/session-config` | `sessions` when local session metadata is missing and backend hydration is needed | `modelCatalog` for context-window/runtime-limit policy if moved out of Core | `sessions` is already behind `backendAdapter.sessions`. Runtime-limit validation is still direct/Core. |
| `POST /api/chat/session/cancel` | `checkpoints` | none | Server now calls `backendAdapter.checkpoints`. |
| `GET /api/chat` | none | none | Core-only usage hint. |
| `POST /api/chat` | `auth`, `identity`, `sessions`, `checkpoints` | `providerCredentials`, `capabilities`, `modelCatalog`, `projects`, `a2a` | `auth`, `identity`, `sessions`, `checkpoints`, `providerCredentials`, `capabilities`, and `modelCatalog` are now behind `backendAdapter`. Project attachment and A2A policy remain Core/direct pending later slices. |
| `POST /api/a2a/v1/message:send` | Same as `POST /api/chat` for Pi-backed execution | `a2a` for backend-owned A2A routing/agent discovery policy | Public A2A wrapper routes into the same runtime execution path today. |
| `POST /api/a2a/v1/message:stream` | Same as `POST /api/chat` for Pi-backed execution | `a2a` for backend-owned A2A routing/agent discovery policy | Public A2A streaming wrapper routes into the same runtime execution path today. |
| `POST /api/a2a/v1` JSON-RPC `SendMessage` / `message/send` | Same as `POST /api/chat` for Pi-backed execution | `a2a` for backend-owned A2A routing/agent discovery policy | JSON-RPC wrapper routes into the same runtime execution path today. |
| `POST /api/a2a/v1` JSON-RPC `SendStreamingMessage` / `message/stream` | Same as `POST /api/chat` for Pi-backed execution | `a2a` for backend-owned A2A routing/agent discovery policy | JSON-RPC streaming wrapper routes into the same runtime execution path today. |
| `GET /api/a2a/v1/agent-card` extended agent card | `sessions` | `a2a` for backend-owned card/routing enrichment | `sessions.fetchAgentCard` is already behind `backendAdapter.sessions`. |
| `GET /api/a2a/v1/tasks` | none | `a2a` only if task state becomes backend-owned | Core in-memory task registry today. |
| `GET /api/a2a/v1/tasks/{taskId}` | none | `a2a` only if task state becomes backend-owned | Core in-memory task registry today. |
| `POST /api/a2a/v1/tasks/{taskId}:cancel` | none for local active-run cancellation | `checkpoints` if cancellation must propagate to a backend runtime holder; `a2a` if task state becomes backend-owned | Core local cancellation today. |
| `GET` or `POST /api/a2a/v1/tasks/{taskId}:subscribe` | none | `a2a` only if task state/subscriptions become backend-owned | Core in-memory task registry today. |
| `GET` or `POST /api/a2a/v1/tasks/{taskId}/pushNotificationConfigs` | none | `a2a` only if push config state becomes backend-owned | Core in-memory push config registry today. |
| `GET` or `DELETE /api/a2a/v1/tasks/{taskId}/pushNotificationConfigs/{configId}` | none | `a2a` only if push config state becomes backend-owned | Core in-memory push config registry today. |

The immediate rule for implementation is:

- Astro Core endpoints that are local/runtime-only must not require a backend adapter.
- Pi/session execution endpoints require `auth`, `identity`, `sessions`, and `checkpoints`.
- Backend-owned model/provider/session enrichment must be behind optional adapter capabilities.
- Missing required capabilities must fail with a clear unsupported-backend/configuration error.
- Missing optional capabilities must degrade explicitly: omit backend-enriched data, report unavailable
  backend credential status, or skip backend-specific routing.

### First Implementation: Main Sequence Backend Adapter

- [x] Make `adapters/mainsequence` export a single Main Sequence adapter object that implements the
  generic backend adapter contract.
- [x] Keep the first Main Sequence adapter implementation behavior-preserving. It should wrap
  existing modules before moving logic.
- [x] Set `ASTRO_BACKEND=mainsequence` as the only supported backend initially.
- [x] Keep the default backend as `mainsequence` until a separate migration changes deployment
  defaults.

### Adapter Slice 1: Auth And Session Authority

- [x] Move `interface/stream/mainsequence-agent-registration.ts` behind
  `mainsequenceAdapter.sessions` and `mainsequenceAdapter.identity`.
- [x] Keep Main Sequence user/session authority behind adapter identity policy without exposing a
  semantic Agent identity generator.
- [x] Move `resolveMainsequenceUserId(...)` behind adapter identity/session policy.
- [x] Move `fetchBackendAgentSession(...)` and `fetchBackendAgentSessionAgentCard(...)` behind
  `adapter.sessions`.
- [x] Move `adapters/mainsequence/runtime-auth.ts` behind `adapter.auth`.
- [x] Change `interface/stream/server.ts` to call `backendAdapter.auth` and
  `backendAdapter.sessions` instead of importing Main Sequence modules directly.
- [x] Change subprocess/Pi launch env construction to call `backendAdapter.auth.buildSubprocessEnv`
  instead of `buildMainsequenceStoredAuthEnv(...)` directly.
- [x] Keep public endpoint behavior and response shapes unchanged during this slice.
- [x] Add regression tests proving chat/A2A/session-model flows still use existing Main Sequence
  session authority.

### Adapter Slice 2: Checkpoints, Credentials, Capabilities, Models

- [x] Move `interface/stream/session-checkpoint-client.ts` behind `adapter.checkpoints`.
- [x] Move `interface/stream/model-provider-credentials-client.ts` behind
  `adapter.providerCredentials`.
- [x] Move `interface/stream/session-capabilities.ts` behind `adapter.capabilities`.
- [x] Move model catalog/session model binding policy behind `adapter.modelCatalog` where the logic
  depends on Main Sequence provider/model metadata.
- [x] Update `interface/stream/server.ts` so Astro Core calls adapter capabilities through a single
  adapter object.
- [x] Add adapter contract tests proving the default Main Sequence adapter exposes required
  capabilities and missing required capabilities fail with a clear error.

### Adapter Slice 3: Bootstrap

- [x] Keep generic Pi filesystem/bootstrap in Astro Core.
- [x] Move Main Sequence CLI config linking into `adapter.bootstrap`.
- [x] Move managed `mainsequence` shim creation into `adapter.bootstrap`.
- [x] Move Main Sequence CLI auth repair command wiring into `adapter.bootstrap`.
- [x] Move Main Sequence workspace directory links into `adapter.bootstrap`.
- [x] Move runtime credential exchange loop startup into `adapter.auth` or `adapter.bootstrap`.
- [ ] Make Astro Core startup work without the `mainsequence` Python package when no Main Sequence
  adapter is enabled.
- [x] Ensure adapter bootstrap logs clearly separate Astro Core bootstrap time from Main Sequence
  adapter bootstrap time.
- [x] Keep local Docker debug flows working with `ASTRO_BACKEND=mainsequence` and
  `ASTRO_PI_PACKAGE_PATHS=/app/adapters/mainsequence/pi-overlay`.

### Main Sequence Adapter Pi Resource Overlay

- [x] Keep SDK-owned skills source-of-truth in the Main Sequence SDK/CLI.
- [x] Delegate SDK-owned skill discovery/copying to the overlay extension instead of hard-coding SDK
  skill slugs in Astro Core bootstrap.
- [x] Keep Main Sequence runtime-specific prompts/tools/hooks out of root `pi/`.
- [x] Move or rename the overlay into the Main Sequence adapter area:
  `adapters/mainsequence/pi-overlay`.
- [x] Update Docker, Compose, GCP, and remote-worker bundle configuration so
  `ASTRO_PI_PACKAGE_PATHS` points at `/app/adapters/mainsequence/pi-overlay`.
- [x] Rename package metadata/descriptions so the overlay is not described as a standalone external
  package.
- [ ] Ensure adapter overlay loading remains behavior-compatible for local, GCP, and project-attached
  Main Sequence deployments.

### Image And Deployment Split

- [x] Add a backend-neutral `astro-core` Docker build stage that does not copy `adapters/mainsequence/pi-overlay`, does not
  set Main Sequence runtime env, and does not install the `mainsequence` Python package.
- [x] Add explicit Main Sequence Docker targets:
  `astro-mainsequence-pi-stream`, `astro-mainsequence-session-checkpoint-sidecar`, and
  `astro-mainsequence-pi`.
- [x] Keep current compatibility target aliases:
  `astro-base`, `astro-runtime`, `astro-pi-stream`, `astro-session-checkpoint-sidecar`, and
  `astro-pi`.
- [x] Keep the current GCP deployment Main Sequence-backed by selecting
  `astro-mainsequence-pi-stream` while preserving the published image name
  `astro/astro-pi-stream`.
- [x] Keep local Compose Main Sequence-backed by selecting the explicit Main Sequence Docker targets
  and setting `ASTRO_BACKEND=mainsequence`.
- [ ] Make a backend-neutral Astro stream runtime boot and serve backend-free endpoints with no
  Main Sequence adapter selected.
- [x] Point the Main Sequence image at the adapter-owned Pi overlay path:
  `/app/adapters/mainsequence/pi-overlay`.
- [x] Build a Main Sequence image variant that installs Astro Core, the Main Sequence Astro adapter,
  and the adapter-owned Pi overlay.
- [ ] Make checkpoint sidecar behavior backend-adapter driven.
- [x] Keep existing Main Sequence deployment env names working during migration.
- [ ] Document final package/adapter selectors only after the package and adapter mechanism exists.

### Acceptance Criteria

- [x] `interface/stream/server.ts` does not import Main Sequence backend clients for auth,
  identity, sessions, checkpoints, provider credentials, session capabilities, or model catalog
  access directly.
- [ ] Astro Core can start with a non-Main Sequence or null backend adapter for endpoints that do not
  need backend policy.
- [x] Main Sequence behavior remains unchanged when `ASTRO_BACKEND=mainsequence` for implemented
  adapter slices.
- [x] Existing backend-backed sessions continue to use `AgentSession.uid`.
- [x] Existing checkpoint, provider credential, capability, and model catalog behavior remains
  compatible through the adapter.
- [ ] A2A backend policy and project attachment behavior move behind adapter capabilities.
- [x] Public endpoints remain unchanged.
- [x] No public runtime attachment endpoints are reintroduced.
- [ ] Main Sequence adapter-owned Pi resources load through the Main Sequence deployment without
  requiring root `pi/` to contain Main Sequence-specific resources.

## Compatibility Expectations

The refactor must preserve current Main Sequence behavior during migration:

- Existing backend-backed sessions continue to use `AgentSession.uid`.
- Existing A2A `message:send` behavior continues.
- ADR 44 agent-targeted sessionless response behavior continues; the unscoped model route remains absent.
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
- Main Sequence runtime-specific Pi behavior becomes explicit as adapter/deployment overlay content.
- SDK-owned skills remain portable through the Main Sequence SDK/CLI instead of being forked into
  Astro.
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

adapters/mainsequence or @mainsequence/astro-adapter
  Main Sequence backend/runtime adapter for Astro
  Main Sequence Pi resource overlay for Astro deployments

mainsequence SDK/CLI
  source of truth for SDK-owned skills and prompts
```

If the extraction is accepted, this ADR becomes the boundary for the refactor. If not, the better
path is cleanup inside the current Main Sequence-specific `astro` package.
