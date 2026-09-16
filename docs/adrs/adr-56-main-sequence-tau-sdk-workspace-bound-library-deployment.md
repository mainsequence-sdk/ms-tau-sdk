# ADR 56: Create the Workspace-Installed Main Sequence TAU SDK and Retire Astro

Status: Accepted

Date: 2026-09-16

Implementation Status: In progress — Phase C0/A0/T0/D0 foundation work started on 2026-09-16;
repository-owned container and image-publication assets were eliminated during C0;
baseline and initial disposition ledgers are under `docs/migration/adr-56/`.

Owners: Main Sequence TAU SDK, tdag-django CodeRepository deployment, and Runtime Infrastructure

Supersedes, when accepted and implemented:

- the repository-independent deployment permitted by ADR 39;
- the `astro-orchestrator` and `code-repository-executor` backend/runtime identities retained by
  ADRs 39 and 40;
- the standalone Astro image, executor bundle, and remote-worker overlay ownership in ADR 50; and
- the deployment-controlled project-extension opt-in and CodeRepository Executor terminology in
  ADR 52.

Migration inputs requiring individual disposition; listing them here does not adopt them into the
new project:

- `docs/adrs/adr-50-lean-python-runtime-abi.md`
- `docs/adrs/adr-52-enable-repository-tau-extensions-in-code-executors.md`
- `docs/adrs/adr-53-retire-agent-capability-registry.md`
- `docs/adrs/adr-55-minimize-bundled-tool-catalog.md`
- `docs/reference/adr-39-unified-pi-runtime-context.md`
- `docs/reference/adr-40-general-pi-deployment-runtime-package-boundary.md`

## Context

The current deployment model treats Astro as both a Python application and an image product.
Main Sequence currently publishes:

1. a standalone Astro runtime image;
2. a scratch `code-repository-executor-bundle` containing Astro source and wheels; and
3. a `Dockerfile.remote-worker` recipe that combines that bundle with a separately built
   CodeRepository image.

The final repository runtime is therefore produced by a second image build:

```text
Astro source
  -> Astro wheelhouse and executor bundle image

CodeRepository source
  -> CodeRepository base image

executor bundle image + CodeRepository base image
  -> remote-worker overlay build
  -> final deployed image
```

This composition has real operational costs:

- there are multiple image digests and build histories for one deployed application;
- failures can come from the project build, Astro bundle publication, overlay ABI validation, or
  the final overlay build;
- the effective Python environment is assembled by two owners at two different times;
- an Astro release can change a project deployment without the dependency appearing in the
  project's own manifest or lock file;
- local reproduction requires knowledge of platform-owned image composition rather than only the
  project checkout; and
- version, source-revision, prompt, extension, and dependency observability must be reconstructed
  across layers.

The current product model also permits two deployment identities:

```text
astro-orchestrator
code-repository-executor
```

ADR 39 correctly recognized that these are not two fundamentally different Tau runtimes, but it
retained repository-independent execution and preserved both names as backend metadata. That
compromise is no longer useful. A coding agent without a specific workspace has no project-local
code, instructions, dependencies, or extension boundary. It is also the reason the deployment
system must maintain a standalone Astro image in addition to project images.

At the same time, a CodeRepository already executes arbitrary project Python and shell code with
the runtime container's identity. Treating the Main Sequence integration library as protected from
that same project, or treating `.tau/extensions` as a separate trust class, does not create a real
security boundary. The meaningful boundary is the container and its scoped Main Sequence runtime
identity versus the control plane, not the SDK package versus code in the same container.

ADR 55 established that optional workload tools belong to the project. This proposal does not turn
the rest of Astro into project code and does not reduce it to a thin transport helper. The existing
Astro implementation and defaults become prepackaged primitives in an installable library. The
project selects the library version and may override or extend its Tau behavior through Tau's one
normal project configuration model.

## Current-State Gap Analysis

| Dimension | Current state | Target state | Gap to close |
| --- | --- | --- | --- |
| Product | Astro runtime/image product | New Main Sequence TAU SDK project | Establish a new project charter, package, API, ADR set, tests, documentation, release process, and operational identity. |
| Python dependency | Platform injects `mainsequence-astro` from an executor bundle | Project declares `ms-tau-sdk` | Publish an installable SDK and remove platform-side package injection. |
| Runtime topology | Repository-independent and repository-attached deployments | Every deployment is bound to one workspace revision | Require CodeRepository branch/workspace identity throughout deployment and routing. |
| Runtime identity | `astro-orchestrator` and `code-repository-executor` | No product-role runtime discriminator | Remove both values and derive execution context from the required workspace binding. |
| Image publication | Standalone image, bundle image, overlay recipe, final project image | One project build producing one deployable project image | Delete Astro image publication and overlay composition. |
| Python environment | Project dependencies and injected Astro wheels are composed by different owners | The project dependency manifest and lock produce one environment | Move SDK and Tau version selection into the project build. |
| Tau customization | Astro injects package defaults while only some project `.tau` resources are enabled | SDK defaults plus project overrides resolved by Tau's normal configuration precedence | Make the existing SDK primitives overridable without creating a second configuration system. |
| Optional tools | Historically bundled, now being removed by ADR 55 | Project-owned extensions and dependencies | Complete ADR 55 while retaining all non-removed Astro functionality in the SDK. |
| Trust model | Some project code is treated as if isolated from the runtime package | SDK and project code share one trusted application boundary | Remove false intra-container protection while retaining control-plane authorization. |
| Observability | Effective runtime assembled across image layers | One project commit, image digest, lock, SDK version, Tau version, and `.tau` digest | Report the actual project-selected composition at startup and in health metadata. |

## Ontology Reset and New-Project Boundary

This ADR changes the ontology and spirit of the product. It is not a package rename, a new major
version of the same deployment product, or an exercise in moving unchanged files from `astro` to
`ms_tau_sdk`.

The Astro ontology is:

```text
platform-deployed runtime product
  -> standalone image and executor bundle
  -> optional workspace attachment
  -> platform-selected and platform-injected runtime version
  -> orchestrator/executor deployment identities
```

The Main Sequence TAU SDK ontology is:

```text
project-installed library product
  -> prepackaged Python primitives and executable service entrypoint
  -> workspace required for every deployment
  -> project-declared and project-locked SDK version
  -> Tau-native project configuration and extensions
  -> one project image with no SDK image composition
```

Because the unit of ownership changes from a platform deployment artifact to a project dependency,
**Main Sequence TAU SDK is a new project**. Astro reaches end of life when the migration completes.
The new project may preserve Git history and may migrate substantial proven implementation, but
history and code reuse do not make the old product ontology normative.

The new project therefore receives its own:

- project charter and architectural vocabulary;
- Python distribution, import namespace, and public API;
- semantic-version series and compatibility policy;
- executable entrypoint and configuration contract;
- ADR index and active decision set;
- test strategy, fixtures, coverage baseline, and quality gates;
- documentation root, examples, and migration guide;
- CI, package publication, release, ownership, and security processes; and
- operational identity, metrics, dashboards, alerts, and support policy.

The implementation must not describe this as "Astro with a different Dockerfile." The accepted
description is that proven Astro behavior is selectively migrated into a new SDK whose public
boundary, deployment ownership, workspace requirement, configuration model, and lifecycle are
defined by this ADR.

ADR 56 is the transition decision in the Astro repository. When the Main Sequence TAU SDK project
root and ADR index are created, this decision must be reissued there as the founding architecture
record, normally ADR 0001, with a link back to this transition record. The new project's ADR
sequence must not imply that every Astro ADR was inherited.

## Prior Artifact Disposition Rules

Nothing from Astro becomes normative in Main Sequence TAU SDK merely because it already exists.
Before migration, each prior artifact must appear in a reviewed disposition matrix with its owner,
target, rationale, and removal or verification evidence.

### Prior ADRs

Every Astro ADR and historical design record must be classified as exactly one of:

1. **Re-adopted**: the decision is independent of the retired deployment ontology and is accepted
   explicitly into the new project.
2. **Amended and reissued**: the behavior remains valuable, but terminology, ownership,
   assumptions, package boundaries, tests, or consequences must be rewritten for the SDK
   ontology.
3. **Superseded**: a new Main Sequence TAU SDK ADR replaces the decision.
4. **Eliminated from the active decision set**: the decision exists only to support Astro images,
   optional workspace deployment, the legacy role split, platform package injection, or another
   removed concept.

There is no implicit fifth category called "carried forward because it was accepted before."

Eliminating an ADR from the active set does not require falsifying history. Historical Astro
records may remain in a clearly non-normative archive for provenance, but they must be removed
from the new project's active ADR index and must not constrain new code or tests. If a historical
file is copied into the new project, its status must say `Superseded`, `Historical`, or
`Not applicable`; an unreviewed `Accepted` status is forbidden.

At minimum, the audit must cover ADRs 47 through 55 and the referenced ADRs 39 and 40. This ADR
already supersedes their standalone/no-workspace, image-composition, legacy-role, and
deployment-controlled extension assumptions. Their protocol, persistence, provider-control, A2A,
and tool-boundary decisions still require individual re-adoption or amendment; this ADR does not
silently approve them as a group.

### Source code and public APIs

Existing source is migration input, not the new project's module architecture. Each subsystem is
classified as:

- migrate as a public SDK primitive;
- migrate behind a new private boundary;
- refactor before migration because it embeds Astro deployment assumptions;
- replace with Tau-native or Main Sequence platform functionality; or
- delete because it exists only for the retired product.

The migration must not preserve an import, setting, environment variable, singleton, side effect,
or extension seam only because current tests exercise it. Public API is intentionally redesigned
and documented for `ms_tau_sdk`; compatibility with `astro.*` requires an explicit migration
decision and is not the default.

### Tests and fixtures

The Astro test suite is evidence about current behavior, not automatically the test suite of the
new project. Every test and fixture is classified as:

- **portable contract test**: preserves externally required transport, auth, provider, session,
  persistence, A2A, or streaming behavior and is ported to the new public boundary;
- **rewritten SDK test**: validates valuable behavior but must be rewritten around
  `ms_tau_sdk`, project installation, workspace binding, or Tau-native configuration;
- **temporary migration test**: compares old and new implementations during the bounded cutover
  and is deleted afterward; or
- **deleted legacy test**: asserts Astro images, executor bundles, overlay builds, optional
  workspace behavior, retired role names, old imports/commands/settings, or other eliminated
  ontology.

Passing the old suite is not sufficient proof that the new SDK is correct, and preserving every
old test is not a goal. Conversely, deletion requires a recorded reason so behavior is not lost by
accident. The new project establishes coverage and quality gates against its public primitives and
supported runtime contracts rather than inheriting a percentage detached from the new structure.

### Documentation, examples, CI, and operations

Documentation, examples, build scripts, CI workflows, release jobs, dashboards, alerts, deployment
templates, environment samples, and runbooks follow the same disposition rule: re-adopt, amend,
replace, or eliminate. Mechanical terminology replacement is insufficient. Material that teaches
the image product, no-workspace operation, the two legacy identities, or SDK overlay injection is
removed from active guidance even if its commands still happen to run during migration.

## Decision

### 1. Create Main Sequence TAU SDK as a new project

The new project is **Main Sequence TAU SDK**. Astro is the source product being retired, not the
long-term project name or compatibility boundary.

The canonical Python distribution name is:

```text
ms-tau-sdk
```

The target Python import package and console command are:

```text
ms_tau_sdk
ms-tau
```

The current names are retired:

```text
mainsequence-astro
astro
astro-stream
```

The SDK selectively migrates the current Astro implementation as reusable, prepackaged primitives:
the FastAPI/ASGI application, A2A and chat transports, Main Sequence authentication, provider
resolution, session persistence, task controls, packaged Tau defaults, resource loading, runtime
management, and the code that creates Tau sessions. Calling it an SDK means that these capabilities
are installed into and launched from the user's project; it does not mean the package is limited
to passive client helpers.

This is a new product/project with a hard migration from the old one, not a second indefinitely
maintained package. A short-lived Astro release or package tombstone may direct maintainers to
`ms-tau-sdk`, but production builds must not install both distributions or retain two import trees.

### 2. Every deployed TAU service is workspace-bound

Every durable Main Sequence TAU deployment must be bound to exactly one verified CodeRepository
branch and workspace revision before it starts.

The semantics that previously belonged to a CodeRepository Executor become the only supported
runtime semantics:

```text
required CodeRepository
required branch/ref
required verified commit
required workspace cwd
one project image
one project-selected Python environment
```

The name `code-repository-executor` does not survive merely because its workspace behavior does.
There is one workspace-bound Main Sequence TAU service, not two agent products.

A repository-independent runtime is removed. An operation that needs orchestration behavior must
run from a real project workspace. If Main Sequence needs a platform-maintained orchestrating
agent, that agent is an ordinary, versioned CodeRepository project that declares `ms-tau-sdk`; it
is not a special no-workspace deployment class.

Agent-targeted sessionless endpoints, if retained, are served by the same workspace-bound service.
They do not justify a standalone runtime. A request whose target cannot be resolved to the
service's required workspace binding is rejected rather than routed to a generic Astro instance.

### 3. Remove both deployment identity concepts

The following concepts disappear from the runtime and backend deployment model:

```text
astro-orchestrator
code-repository-executor
```

They must not remain as aliases for new runtime types, image targets, environment switches,
default agent types, routing branches, catalog entries, analytics categories, or compatibility
modes.

Workspace identity is represented directly by the CodeRepository, branch/ref, commit, and service
binding. Runtime behavior is derived from those facts instead of an `agent_type` string. Where the
backend requires a normal domain object for ownership or routing, it should use a single
workspace-bound TAU service/agent relation and not introduce another role discriminator that
recreates the same split under a new name.

The exact Django schema migration belongs to the coordinated backend implementation ADR, but its
required end state is fixed here: no deployed service is valid without a workspace, and neither
legacy identity is part of the steady-state API.

### 4. Projects explicitly install `ms-tau-sdk`

The CodeRepository project owns the SDK dependency like every other application dependency:

```toml
[project]
dependencies = [
  "ms-tau-sdk==<project-selected-version>",
]
```

Equivalent locked dependency declarations are valid for the project's chosen Python package
manager. The important rules are:

- the dependency is visible in the repository;
- the project selects and locks the version;
- the normal project build installs it into the same Python environment as project code;
- the platform does not inject, pin, replace, or upgrade the SDK behind the project's dependency
  resolver; and
- startup never downloads or mutates Python packages.

The platform may publish a compatibility range and reject a build or deployment whose installed
SDK cannot speak the current backend protocol. It must report that incompatibility clearly. It
must not silently repair it by overlaying a different SDK version.

If a repository does not declare a compatible `ms-tau-sdk`, it is not deployable as a Main
Sequence TAU service. There is no hidden image fallback.

### 5. Stop publishing and composing Astro images

Main Sequence will no longer build or publish:

- a standalone Astro runtime image;
- an `astro` image target;
- a `code-repository-executor-bundle` image;
- an Astro wheelhouse embedded in an OCI bundle;
- `Dockerfile.remote-worker`; or
- an image overlay whose purpose is to install Astro into a CodeRepository image.

The target build is:

```text
CodeRepository checkout
  + project dependency manifest and lock
  + explicit ms-tau-sdk dependency
  + project-owned .tau resources
  -> one normal CodeRepository build
  -> one deployable project image
  -> launch ms-tau from that image
```

The CodeRepository build itself may use normal Docker multi-stage techniques. The prohibited
pattern is the current product-level second composition step in which a platform Astro artifact is
mixed with an already built project image.

A neutral, documented Python base image may still be offered by Main Sequence as a convenience.
It is infrastructure, not a TAU/SDK image, contains no `ms-tau-sdk`, and is not required when a
project supplies another compatible base.

### 6. `ms-tau-sdk` provides the complete prepackaged TAU runtime library

The new project selectively migrates the non-deployment functionality proven in this repository.
`ms-tau-sdk` provides it behind reusable Python primitives and a standard executable entrypoint,
including:

- the application factory and server lifecycle;
- HTTP, streaming, chat, and A2A transport surfaces;
- runtime-credential authentication and backend authorization adapters;
- backend session attachment and durable persistence;
- provider/model resolution and credential exchange;
- Main Sequence MCP discovery;
- protocol-required task-control tools and task lifecycle behavior;
- the default Tau instructions and resource composition currently shipped by Astro;
- Tau session construction, runtime management, cancellation, and cleanup;
- health, readiness, compatibility, and runtime-composition metadata; and
- the other non-deployment behavior explicitly accepted by the artifact-disposition audit.

Projects consume these primitives either through the `ms-tau` entrypoint or through documented
Python APIs when they need to embed or compose the service. Moving from an image product to a
library must not require every project to reimplement the server, transport, persistence, or Tau
integration.

ADR 55 remains responsible for the deliberate removal of optional workload-tool baggage.
Filesystem coding tools remain Tau core capabilities, and Main Sequence platform actions arrive
through the Main Sequence MCP boundary. Web access, media handling, data-domain helpers,
repository-specific commands, and similar conveniences are installed by the project. Other Astro
behavior is migrated only when the disposition audit accepts it into the new project; the delivery
change alone is neither a reason to lose required behavior nor a reason to preserve obsolete
behavior.

### 7. Project installation and startup process

A TAU-enabled project uses `ms-tau-sdk` as a normal application dependency. The project does not
write its own FastAPI application, Tau bootstrap, streaming adapter, authentication client,
session manager, or persistence implementation.

The repository declares and locks the dependency:

```toml
[project]
dependencies = [
  "ms-tau-sdk==<project-selected-version>",
]
```

It may also commit normal project Tau configuration and extensions:

```text
project/
├── .tau/                 # optional Tau-native overrides and extensions
├── pyproject.toml        # declares ms-tau-sdk
├── uv.lock               # or the project's equivalent dependency lock
└── src/                  # project application code
```

The project's ordinary image build then:

1. starts from a compatible Python base;
2. copies the verified CodeRepository checkout into the workspace;
3. installs the project's locked dependencies, including `ms-tau-sdk`, into one Python
   environment;
4. installs any operating-system utilities required by the project and Tau coding tools; and
5. retains the repository and its `.tau` directory in the final image.

The final image runs from the repository workspace and starts the packaged SDK entrypoint:

```dockerfile
WORKDIR /workspace
CMD ["ms-tau"]
```

The deployment platform may override the container command with the same `ms-tau` entrypoint, but
it does not add another image layer or install another SDK wheel. `ms-tau` defaults its workspace
to the process working directory, so the final `WORKDIR` is the deployed CodeRepository. Any
explicit workspace option must resolve to that same verified checkout.

At deployment time, Main Sequence injects runtime configuration and secrets rather than baking
them into the repository or image. The minimum Main Sequence connection contract remains:

```text
MAINSEQUENCE_BACKEND
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET
```

The two runtime-credential values are exchanged by the SDK for the normal short-lived Main
Sequence bearer/JWT used for backend calls. Provider credentials, session leases, and other
execution evidence continue to come from the authenticated backend bootstrap; they are not
project dependencies or committed secrets.

Starting `ms-tau` performs the same application startup currently performed by `astro-stream`:

```text
ms-tau
  -> create the packaged FastAPI/ASGI application
  -> validate the workspace and runtime credentials
  -> create the Main Sequence backend/auth clients
  -> create the provider factory and session runtime manager
  -> connect required Main Sequence MCP dependencies
  -> expose health, chat, responses, sessions, and A2A routes
  -> report ready
```

Startup makes the service ready; it does not eagerly create a Tau conversation. Durable Tau
sessions continue to load lazily when a chat or A2A request identifies an existing backend session:

```text
request with session_uid
  -> acquire/bootstrap the backend session and runtime lease
  -> hydrate provider control and provider credentials
  -> restore snapshot/history
  -> resolve the one effective Tau configuration
       SDK packaged defaults
       + project .tau overrides/extensions
  -> create coding tools, Main Sequence MCP tools, and task-control tools
  -> CodingSession.load(...)
  -> CodingSession.prompt(...)
  -> stream translated Tau events
  -> persist and settle the turn
```

The existing sessionless `AgentHarness` response path is also retained as a prepackaged SDK
primitive. It continues to serve the agent-targeted response endpoints without requiring the
project to implement another server. Being deployed inside the same workspace image does not turn
a sessionless request into a durable coding session.

For local development, the equivalent target flow is:

```bash
uv add ms-tau-sdk

export MAINSEQUENCE_BACKEND="https://api.main-sequence.app"
export MAINSEQUENCE_RUNTIME_CREDENTIAL_ID="<local-runtime-credential-id>"
export MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET="<local-runtime-credential-secret>"

uv run ms-tau
```

The command is run from the repository root. Runtime credentials must come from the developer's
secret environment or Main Sequence tooling and must never be committed. A valid backend session
and provider configuration are still required for durable execution; installing the library does
not create backend state by itself.

The migration equivalence is therefore:

```text
current
  project image + platform-injected Astro bundle -> astro-stream

target
  project manifest includes ms-tau-sdk -> one project image -> ms-tau
```

All server and Tau integration behavior on the right-hand side comes from the installed library.

### 8. Use one Tau configuration model with project overrides

`ms-tau-sdk` ships its current Tau instructions, prompts, resources, and other defaults as packaged
library primitives. A project that provides no overrides receives those defaults and the same
baseline runtime behavior that Astro provides today.

The workspace's normal `.tau` directory is the customization boundary. Tau resolves one effective
configuration using the SDK's packaged defaults as the base and the project's `.tau` values as
overrides or additions according to Tau's native precedence rules:

```text
ms-tau-sdk packaged Tau defaults
  -> Tau's normal configuration/resource resolution
  -> /workspace/.tau overrides and additions
  -> one effective Tau configuration
```

This includes Tau's general instructions/system-prompt configuration, project instructions,
skills, prompt templates, extensions, hooks, and other configuration supported by the installed
Tau version. There is no separate Main Sequence prompt configuration and no SDK-specific
prompt-file or manifest format. If Tau does not yet expose a native override needed by a project,
the Tau configuration contract must be extended; `ms-tau-sdk` must not solve that gap by inventing
a second competing configuration format.

The SDK owns and preserves its packaged defaults. The project owns only the overrides and
extensions it places in `.tau`. The resolved result is the configuration used to construct Tau.
Authentication, authorization, session ownership, lease handling, persistence invariants, and A2A
state transitions remain SDK code and are not replaced by `.tau` prompt or resource overrides.

Project extension discovery is enabled for every workspace-bound deployment. There is no
`ASTRO_CODE_REPOSITORY_EXTENSIONS_ENABLED`-style platform gate. A project controls its extension
surface by the files and dependencies it commits. Requests and model output still cannot install
new extensions dynamically at runtime.

### 9. Project code and the SDK share one trust boundary

The project, its dependencies, `.tau/extensions`, and `ms-tau-sdk` execute in the same container,
Python environment, Unix identity, and workspace trust boundary. Main Sequence does not claim to
sandbox the SDK from the project that imported it.

This decision does not weaken the actual platform boundary. The following remain enforced outside
prompt and extension policy:

- container isolation and workload identity;
- scoped Main Sequence runtime credentials;
- server-side authorization for Main Sequence resources;
- network and infrastructure controls applied to the project workload;
- backend ownership of provider credential exchange; and
- protocol validation at the transport boundary.

Users are responsible for the dependencies, extensions, tools, and instructions they install in
their project. A faulty or malicious project extension can alter agent behavior, read credentials
available to that workload, crash the process, or corrupt that project's runtime state. That is
already true for arbitrary application code in the same CodeRepository image and must be
documented honestly.

### 10. Observe the actual project-selected runtime composition

To make the simpler build easier to operate, startup and health metadata must report at least:

- CodeRepository identity, branch/ref, and verified commit;
- deployed project image digest;
- installed `ms-tau-sdk` version;
- installed Tau version;
- Python version;
- normalized `.tau` resource/configuration digest;
- the packaged-default version and the project override sources used by Tau's configuration
  resolver;
- effective extension catalog names and digest;
- the effective prompt digest, without logging prompt contents or secrets by default.

These values must come from the running project image. They must not be inferred from a separate
Astro release tag or bundle reference. Logs and traces use Main Sequence TAU SDK terminology and
the workspace identity, not either retired agent type.

## Target Runtime Shape

```text
Main Sequence control plane
  -> selects CodeRepository + branch + commit
  -> builds that project once
       -> installs project dependencies, including ms-tau-sdk
       -> retains project .tau resources
  -> deploys that one project image
  -> starts ms-tau in /workspace

ms-tau-sdk in the project process
  -> supplies the accepted, migrated runtime primitives and Tau defaults
  -> authenticates with scoped runtime credentials
  -> attaches to the workspace-bound Main Sequence session
  -> resolves the provider/model
  -> lets Tau resolve one configuration with project .tau overrides and extensions
  -> exposes Main Sequence chat/A2A transports
  -> persists and reports runtime state
```

There is no standalone Astro service and no post-project Astro overlay build in this shape.

## Consequences

### Benefits

- There is one deployment topology and one workspace model.
- A deployed image can be reproduced from the project revision and its dependency lock.
- The SDK version is visible, inspectable, and reviewable in the project.
- Dependency resolution happens once rather than across a project image and an injected bundle.
- Build failures and vulnerability scans point to one image and one software bill of materials.
- Projects receive the existing Astro behavior by default and can override or extend Tau through
  its normal project configuration.
- Monitoring can identify the exact SDK, Tau, project, and `.tau` composition actually running.
- Optional tool baggage cannot silently return through the base runtime image.

### Costs and limitations

- Every TAU-enabled project must include and maintain a Python environment compatible with the SDK.
- Non-Python repositories need an explicit Python service/runtime layer inside their project image;
  there is no platform overlay that supplies one afterward.
- SDK and Tau versions may differ across projects. Main Sequence must publish a compatibility
  matrix and support policy.
- SDK security fixes are project dependency upgrades. The platform may block protocol-incompatible
  versions at build/deployment time, but it cannot pretend an unmodified project is running a
  different dependency.
- Project image builds may become larger or slower because each project resolves the SDK and Tau.
  Shared registry/build caches may optimize this without changing ownership.
- A broken `.tau` override, extension, or dependency can prevent startup or degrade agent behavior.
  That failure belongs to the project and must be visible in project build/startup logs.
- There is no no-workspace escape hatch. Platform-owned orchestration behavior requires a normal
  platform-owned repository and deployment.
- Renaming the distribution, imports, commands, configuration prefix, metrics, and backend schema
  is a coordinated breaking migration.

## Compatibility and Migration

This proposal is intentionally a hard architectural cut. The steady state has no mixed deployment
mode.

Implementation must be coordinated in this order:

1. Establish the new-project charter and complete the initial ADR, source, test, documentation,
   deployment, and operations disposition matrices.
2. Create the `ms-tau-sdk` distribution with the `ms_tau_sdk` import package and `ms-tau`
   entrypoint from explicitly accepted implementation.
3. Package accepted Astro behavior and Tau defaults as SDK primitives, then make Tau's normal
   project `.tau` precedence the only override mechanism. Extend Tau itself if a required
   general-configuration override is missing.
4. Add runtime composition reporting and an explicit SDK/backend protocol compatibility handshake.
5. Update each TAU-enabled CodeRepository to declare and lock `ms-tau-sdk`, provide any required
   `.tau` resources, and start `ms-tau`.
6. Change tdag-django to build and deploy the project image directly, with a required workspace
   binding and no executor overlay build.
7. Migrate backend service/session ownership away from both legacy agent types. Existing
   repository-independent sessions must not be silently assigned to an arbitrary project; they
   require an explicit archive or project-migration policy.
8. Stop publishing and accepting the standalone Astro image, executor bundle image, and remote
   worker recipe.
9. Remove the `mainsequence-astro` distribution, `astro` import tree, `astro-stream` entrypoint,
   Astro image files, and Astro-prefixed configuration after all target projects have migrated.
10. Remove legacy backend fields, validation branches, catalog entries, metrics dimensions, and
   documentation for `astro-orchestrator` and `code-repository-executor`.
11. Establish the final Main Sequence TAU SDK repository/project identity, active ADR index,
    documentation root, CI, and release process. Preserve Git history if useful, but do not present
    the result as an ordinary Astro major-version rename.

No compatibility layer may reintroduce platform-side SDK injection or allow a deployment without
a workspace. Temporary migration telemetry may recognize legacy values only to inventory and
reject them with an actionable error.

## Required Cross-Repository Work

### Main Sequence TAU SDK repository

- Create the new distribution, import package, CLI, settings, logs, metrics, docs, tests, and active
  ADR index from reviewed migration inputs.
- Expose a stable application/CLI entrypoint suitable for project images.
- Remove the standalone Dockerfile targets, bundle target, remote-worker recipe, and image
  publication workflow.
- Preserve all non-deployment functionality and packaged Tau defaults explicitly accepted by the
  artifact-disposition audit as SDK primitives.
- Route project overrides through Tau's one native configuration model; add no SDK-specific prompt
  configuration.
- Always enable project Tau extension discovery for the verified workspace.
- Preserve transport, auth, persistence, provider, MCP, A2A, runtime-management, and default
  resource responsibilities.
- Emit the runtime-composition metadata defined by this ADR.

### tdag-django and deployment infrastructure

- Require CodeRepository branch/workspace binding for every TAU service.
- Remove both legacy `agent_type` concepts and their routing/defaulting behavior.
- Remove executor-bundle inputs, overlay Dockerfile download, overlay build jobs, and Astro image
  catalog configuration.
- Build the project once from its own dependency manifest and deploy that resulting image
  directly.
- Validate the installed SDK protocol compatibility without changing the project's selected
  package version.
- Define explicit migration/archive handling for no-workspace agents and sessions.

### TAU-enabled project repositories

- Declare and lock `ms-tau-sdk`.
- Supply a compatible Python runtime and all optional system dependencies.
- Own any `.tau` overrides, additions, extensions, and extension dependencies the project chooses
  to provide.
- Upgrade the SDK intentionally when adopting fixes or new platform protocol versions.

## Phased Implementation Plan

The target architecture is a hard cut, but the refactor that reaches it is deliberately phased.
"Hard cut" means the final production state has no dual deployment modes, legacy role aliases, or
hidden SDK injection. Repository-owned Docker, Compose, Kubernetes, image-publication, overlay, and
runtime-wheelhouse assets are deleted immediately because they are not inputs to the new SDK. The
Python refactor, backend schema change, and project migration remain phased.

Each phase must preserve the behavior owned by the previous phase, add tests for its new boundary,
and satisfy its exit gate before the next dependent phase begins. Code, deployment, and
documentation changes are tracked separately because documentation must remain truthful while the
implementation is temporarily between the current and target architectures.

### SDK code and coordinated platform refactor phases

#### Phase C0: Freeze the reusable SDK behavior

- Write the Main Sequence TAU SDK project charter and the ADR, source, test, documentation, and CI
  disposition matrices required to create the library.
- Inventory only behavior and Python resources that may belong to the SDK: the current `astro`
  import surface, `astro-stream` command, FastAPI routes, settings, packaged Tau resources, tools,
  authentication, provider hydration, persistence, leases, snapshots, A2A behavior, sessionless
  behavior, and lifecycle/shutdown semantics.
- Delete repository-owned Dockerfiles, Docker/Compose configuration, Kubernetes manifests, image
  publication definitions, overlay recipes, runtime wheelhouse locks, image verification scripts,
  and deployment-only tests. Git history is sufficient provenance; these artifacts are not
  migration inputs and receive no replacement inside the SDK repository.
- Add or retain characterization tests for both Tau execution paths: durable `CodingSession` and
  sessionless `AgentHarness`.
- Record the current HTTP/OpenAPI surface and representative streaming event fixtures so the
  packaging refactor cannot silently change the protocol.

Exit gate: the reusable Python behavior has a green reproducible test baseline, the new-project
charter is approved, every candidate SDK artifact has an initial proposed disposition and owner,
and the SDK repository contains no Docker, Compose, Kubernetes, image-publication, overlay, or
runtime-wheelhouse artifact.

#### Phase C1: Establish internal library boundaries without changing behavior

- Separate application creation, CLI startup, transport routers, runtime/session management,
  backend/auth clients, provider construction, persistence, Tau resources, and observability into
  explicit Python modules with documented responsibilities.
- Remove unnecessary import-time side effects so applications and tests can call an application
  factory without starting network work.
- Define public construction primitives for settings, the FastAPI application, the runtime
  manager, durable Tau sessions, and sessionless harness execution.
- A temporary extraction harness may keep the current `mainsequence-astro`, `astro`, and
  `astro-stream` names during this phase so the structural refactor is tested independently from
  the new project boundary. Those names are not accepted as public APIs of the new project.

Exit gate: the current Python command and application factory run through the new internal
boundaries with unchanged routes, streaming, authentication, and persistence behavior.

#### Phase C2: Consolidate Tau configuration and project extension behavior

- Express the SDK's existing Tau instructions and resources as packaged defaults consumed by
  Tau's normal configuration resolver.
- Make the workspace `.tau` directory the single native project override/extension surface.
- Remove the deployment-specific project-extension opt-in from the target code path; the verified
  workspace itself is the trust decision.
- Add precedence, diagnostics, reload, tool-catalog, and failure-isolation tests.
- If Tau cannot override part of its general configuration natively, implement that capability in
  Tau first rather than adding a Main Sequence-specific second configuration format.

Exit gate: a project with no `.tau` overrides matches the current packaged behavior, while a
project with native `.tau` overrides receives the documented Tau result.

#### Phase C3: Create the `ms-tau-sdk` package and public Python API

- Create the new `ms-tau-sdk` distribution and `ms_tau_sdk` import tree from the subsystems accepted
  by the disposition audit.
- Publish deliberate public Python APIs for embedding the application and using the packaged
  primitives; keep internal modules private.
- Add the `ms-tau` executable entrypoint and make repository cwd the default workspace.
- Rename Astro-specific settings, logs, metrics, errors, and runtime identifiers while retaining
  the stable `MAINSEQUENCE_*` credential contract.
- Generate a wheel and source distribution that contain all required Tau defaults and no
  deployment-only Docker assets.
- Test installation into a clean environment so success cannot depend on this source checkout.

Exit gate: a clean fixture project can install the built wheel, import `ms_tau_sdk`, start
`ms-tau`, and serve health without importing `astro`.

#### Phase C4: Prove complete behavioral parity from a project repository

- Create a minimal reference CodeRepository whose only agent-runtime dependency is
  `ms-tau-sdk`.
- Set the final image `WORKDIR` to `/workspace` and its command to `CMD ["ms-tau"]`.
- Exercise runtime-credential exchange, readiness, Main Sequence MCP discovery, provider
  hydration, durable session bootstrap, snapshot/history restore, tools, live streaming,
  cancellation, persistence settlement, eviction, and shutdown.
- Exercise the sessionless `AgentHarness` routes independently.
- Verify that the project needs no custom FastAPI, Tau bootstrap, streaming, auth, or persistence
  glue.

Exit gate: the reference project passes the same end-to-end contract suite as the current Astro
deployment.

#### Phase C5: Publish the SDK through the normal Python package channel

- Publish a release candidate to the package index used by CodeRepository builds.
- Publish checksums, provenance, dependency metadata, protocol compatibility, and upgrade notes.
- Prove deterministic installation from a project lock without reading an Astro OCI bundle.
- Add build-cache support around the normal project dependency build without changing dependency
  ownership.

Exit gate: build infrastructure can install a pinned SDK release using only the project manifest,
lock, and configured Python package index.

#### Phase C6: Refactor the CodeRepository image build

- Install all project dependencies, including `ms-tau-sdk`, during the one normal project image
  build.
- Ensure the final image contains the verified repository checkout, `.tau`, compatible Python
  environment, and required operating-system utilities.
- Set or override the container command to `ms-tau` and run it from `/workspace`.
- Record the project commit, dependency lock, image digest, SDK version, Tau version, and effective
  configuration digest.
- Add a build-time compatibility check that reports incompatible SDK/backend protocol versions
  without replacing the project's dependency.

Exit gate: deployment receives one directly runnable project image and no executor-bundle or
remote-worker build input.

#### Phase C7: Refactor Django deployment orchestration

- Remove the executor-bundle image lookup, Astro overlay Dockerfile download, second image build,
  and Astro release tag from the CodeRepository deployment path.
- Require a CodeRepository branch/ref, verified commit, workspace, and direct project image for
  every TAU service.
- Inject the runtime credential ID and secret, backend URL, and deployment metadata into the
  project service.
- Start `ms-tau` in the workspace and use its health/readiness surface before routing traffic.
- Preserve current secret handling, provider control, MCP access, and runtime credential scope.

Exit gate: a staging deployment is created directly from one project image and passes the complete
service contract without any Astro image artifact or overlay build.

#### Phase C8: Refactor backend identity and data ownership

- Introduce the single workspace-bound TAU service/agent relationship required by this ADR.
- Remove runtime and routing behavior keyed by `astro-orchestrator` and
  `code-repository-executor`.
- Make workspace binding mandatory in creation, session attachment, authorization, scheduling,
  and reconciliation paths.
- Classify existing repository-independent agents and sessions for explicit archive or migration;
  never attach them to an arbitrary workspace.
- Migrate analytics and observability dimensions to workspace identity and installed SDK version.

Exit gate: new backend records cannot express a no-workspace TAU deployment or either retired role,
and the legacy-data disposition has been reviewed before destructive migration.

#### Phase C9: Migrate pilot projects

- Select representative projects covering simple Python, large dependency graphs, project
  extensions, custom `.tau` configuration, MCP usage, durable sessions, and sessionless responses.
- Add and lock `ms-tau-sdk` in each pilot repository.
- Build and deploy through the direct project-image path, compare traces and protocol fixtures,
  and run rollback drills before the hard cut.
- Fix SDK or Tau primitives centrally when pilots reveal duplicated project glue; do not normalize
  copy-pasted workarounds into every repository.

Exit gate: pilot projects meet correctness, startup-time, image-size, streaming-latency,
persistence, and observability thresholds.

#### Phase C10: Migrate all projects and cut over production

- Update every TAU-enabled project manifest and lock.
- Rebuild each project once through the direct project-image path.
- Route Agent traffic to the workspace-bound `ms-tau` service.
- Stop creating standalone orchestrators, executor overlays, or services using either retired
  role.
- Keep rollback artifacts only for the bounded cutover window; do not operate both architectures
  as a permanent product option.

Exit gate: the production inventory contains only workspace-bound services running a
project-declared `ms-tau-sdk`.

#### Phase C11: Delete legacy code and publication infrastructure

- Confirm that the SDK repository's C0 deletion of Docker, Compose, Kubernetes, overlay,
  runtime-wheelhouse, and image-publication assets has not been reversed. Delete the corresponding
  executor-bundle and overlay publication machinery from its platform-owning repositories.
- Delete the `mainsequence-astro` production package, `astro` import tree, `astro-stream` command,
  Astro-prefixed steady-state settings, and obsolete compatibility branches.
- Delete legacy Django fields, image catalogs, deployment jobs, metrics dimensions, and role
  validation after their data migration completes.
- Retain only an explicit package tombstone or migration error where required; it must not run the
  old architecture.

Exit gate: repository and cross-repository searches find no executable production dependency on
the old package, images, overlay, commands, settings, or role names.

#### Phase C12: Final release and new-project repository transition

- Publish the first stable Main Sequence TAU SDK release from the final package layout.
- Establish the Main Sequence TAU SDK repository/project identity and update CI, release
  automation, ownership metadata, and dependency scanners. Git history may be retained, but the
  repository root, active ADR index, package, docs, and release process must express the new
  project rather than an in-place Astro major version.
- Reissue this transition decision as the founding ADR in the new project's ADR sequence and
  import only those prior decisions explicitly accepted by the ADR disposition process.
- Run clean-room project installation, staging deployment, production smoke, protocol, streaming,
  persistence, and disaster-recovery tests.
- Close the migration only after the observability inventory proves which SDK and Tau version every
  project is running.

Exit gate: Main Sequence TAU SDK is the only supported product and deployment path.

### Documentation refactor phases

Documentation changes are not deferred to the final cleanup. They progress alongside the code,
but target-state material must be labeled until its corresponding code phase is available.

#### Phase D0: Documentation inventory and terminology map

- Inventory the root README, getting-started guides, reference guides, API/interface documents,
  environment-variable reference, folder structure, request lifecycle, deployment examples,
  troubleshooting, implementation-task documents, ADRs, and cross-repository runbooks.
- Produce the prior-ADR disposition matrix and identify which decisions will be re-adopted,
  amended/reissued, superseded, or eliminated from the new project's active decision set.
- Classify each document as current operational truth, historical decision context, target-state
  design, or obsolete material.
- Define the terminology map from Astro and both legacy agent roles to Main Sequence TAU SDK and
  workspace-bound service concepts.

Exit gate: every affected document has an owner and destination; no automated global replacement
is used without semantic review.

#### Phase D1: SDK developer documentation

- Document the `ms_tau_sdk` public API, application factory, lifecycle hooks, configuration
  objects, runtime manager, durable-session primitive, sessionless primitive, and `ms-tau`
  entrypoint.
- Clearly distinguish supported public primitives from private implementation modules.
- Add API examples showing how to run the packaged application without rebuilding its transport or
  persistence layers.
- Generate or validate reference documentation against the actual exported package surface.

Exit gate: an SDK maintainer can understand and test the library boundary without consulting the
old image implementation.

#### Phase D2: Project-author installation and customization guide

- Document adding and locking `ms-tau-sdk` with supported package managers.
- Document the required workspace layout, Python/runtime prerequisites, `WORKDIR`, `ms-tau`
  command, local runtime credentials, and prohibition on committing secrets.
- Document Tau's one `.tau` configuration/precedence model, project extensions, skills, prompts,
  dependencies, diagnostics, and user responsibility for installed code.
- Add minimal, extended, and troubleshooting examples using real fixture projects.

Exit gate: a project author can move from an ordinary repository to a working local TAU service by
following one guide with no knowledge of Astro images.

#### Phase D3: Deployment and operations guide

- Document the single project-image build, runtime credential injection, backend bootstrap,
  readiness, routing, scaling, shutdown, and rollback procedures.
- Document runtime composition metadata: project commit, image digest, SDK version, Tau version,
  `.tau` digest, and extension catalog.
- Replace bundle/overlay debugging with locked-checkout SDK startup and direct project-build
  reproduction.
- Update security guidance to reflect the real project/SDK shared trust boundary and the external
  container/control-plane boundary.

Exit gate: operators can diagnose build, startup, authentication, provider, MCP, session,
streaming, persistence, and extension failures from the one deployed image.

#### Phase D4: API, streaming, and lifecycle documentation

- Preserve and revalidate the chat, A2A, session, health, and agent-response route documentation.
- Document the durable `CodingSession` path separately from the sessionless `AgentHarness` path.
- State which endpoints provide incremental Tau-event streaming and which response surfaces buffer
  before emitting a final event.
- Update request/response examples, OpenAPI references, protocol extensions, cancellation,
  durability, and task lifecycle diagrams.

Exit gate: documented routes and event examples match generated OpenAPI and end-to-end fixtures.

#### Phase D5: Migration and release documentation

- Publish an Astro-to-`ms-tau-sdk` migration guide covering dependency, import, command, settings,
  image, workspace, `.tau`, and backend identity changes.
- Publish a version compatibility matrix and explicit unsupported-version behavior.
- Update the changelog and release notes for every breaking change and removal.
- Provide separate checklists for SDK maintainers, Django/deployment maintainers, operators, and
  project maintainers.

Exit gate: every production project and operator has an actionable migration checklist before the
production cutover.

#### Phase D6: Rewrite current-state documentation and archive history

- Rename the root README and active documentation from Astro to Main Sequence TAU SDK.
- Update folder-structure, scope, environment, request-lifecycle, deployment, extension, and
  troubleshooting documents to describe the library-installed architecture.
- Update `.env.example`, container examples, CI snippets, templates, and code links.
- Mark superseded ADRs and historical reference documents accurately rather than rewriting history
  as if it had never existed.
- Remove obsolete image, bundle, overlay, no-workspace, and legacy-role instructions from active
  guidance.

Exit gate: active documentation describes only the supported target architecture, while historical
records remain clearly labeled and non-normative.

#### Phase D7: Documentation verification and final terminology audit

- Execute every installation, local-run, project-build, deployment, extension, and API example in
  CI where practical.
- Check internal links, generated API references, environment-variable tables, code snippets, and
  command output.
- Search active documentation for stale `mainsequence-astro`, `astro-stream`, standalone image,
  executor bundle, `astro-orchestrator`, and `code-repository-executor` instructions.
- Permit legacy terms only in explicitly historical or migration contexts.

Exit gate: documentation checks pass and a new project can be created, built, started, and used
solely from the published Main Sequence TAU SDK documentation.

### Architecture-decision refactor phases

#### Phase A0: Inventory and dependency-map prior decisions

- Enumerate every active Astro ADR, referenced historical ADR, implementation decision, and
  cross-repository deployment ADR that constrains the current runtime.
- Record which code, tests, schemas, settings, docs, and operational procedures implement each
  decision.
- Identify decision dependencies so one ADR is not re-adopted while silently depending on an
  eliminated Astro concept.

Exit gate: the ADR disposition matrix is complete and every prior accepted decision has an owner.

#### Phase A1: Re-adopt ontology-independent contracts

- Re-evaluate protocol, authentication, provider-control, persistence, A2A, lease, snapshot, and
  streaming decisions against the new SDK project charter.
- Reissue only the decisions that remain valid without standalone images, optional workspaces,
  platform package injection, or the two retired roles.
- Express them using `ms_tau_sdk`, project-installed dependency, and workspace-bound terminology.

Exit gate: every re-adopted decision is testable through the new public SDK or protocol boundary
and contains no hidden dependency on the Astro deployment ontology.

#### Phase A2: Amend decisions whose behavior survives but ownership changes

- Rewrite decisions where the behavior remains valuable but responsibility moves from an image,
  overlay, deployment role, or Astro-global setting into the SDK, project manifest, workspace,
  Tau configuration, or backend.
- Update consequences, rejected alternatives, implementation tasks, verification, and ownership;
  terminology-only amendments are insufficient.
- Link the amended Main Sequence TAU SDK ADR to the prior Astro record for provenance.

Exit gate: each amended decision describes the target architecture as if implemented by the new
project, not as a compatibility appendix to Astro.

#### Phase A3: Supersede or eliminate obsolete decisions

- Mark image-publication, bundle, overlay, no-workspace, legacy-role, and platform-injection
  decisions as superseded or not applicable.
- Remove eliminated decisions from the new project's active ADR index and from active code/test/doc
  requirements.
- Preserve historical copies only in a labeled, non-normative archive when useful for provenance.

Exit gate: no active Main Sequence TAU SDK decision requires an artifact or concept removed by
this ADR.

#### Phase A4: Establish the new ADR baseline

- Reissue this decision as the founding Main Sequence TAU SDK ADR.
- Publish the new active ADR index containing only re-adopted, amended, or new SDK decisions.
- Add an ADR check that rejects active records with stale project names, retired role assumptions,
  or unresolved links to eliminated decisions.

Exit gate: a maintainer can derive the complete normative architecture from the new project's ADR
index without treating the Astro ADR set as implicitly active.

### Test-suite refactor phases

#### Phase T0: Inventory and classify the current test suite

- Map every unit, integration, contract, image, deployment, fixture, and end-to-end test to the
  behavior and ADR it claims to verify.
- Classify each as portable contract, rewritten SDK, temporary migration, or deleted legacy test.
- Record the target file/suite or deletion rationale; unclassified tests cannot simply be copied.

Exit gate: the test-disposition matrix covers the Astro suite and relevant cross-repository tests.

#### Phase T1: Preserve black-box behavioral evidence

- Capture current HTTP/OpenAPI, chat streaming, A2A, sessionless response, authentication,
  provider-control, lease, persistence, snapshot, cancellation, and shutdown behavior as
  implementation-independent fixtures.
- Separate behavior that is intentionally preserved from accidental details such as import paths,
  log wording, image stages, and role names.
- Run these fixtures against the current service to establish the migration baseline.

Exit gate: required external behavior can be checked without importing `astro` internals.

#### Phase T2: Build the new SDK unit-test suite

- Write tests against the new `ms_tau_sdk` public primitives and intentional private boundaries.
- Cover the application factory, settings, CLI, runtime manager, durable `CodingSession`,
  sessionless `AgentHarness`, auth, backend client, providers, storage, task controls, event
  translation, resources, and lifecycle cleanup.
- Eliminate tests whose sole purpose is to preserve obsolete module layout or deployment globals.

Exit gate: the new package has an independent unit suite and quality baseline with no production
import of `astro`.

#### Phase T3: Test Tau configuration and project extension semantics

- Verify SDK defaults with no project overrides.
- Verify Tau-native general configuration, instructions, skills, prompts, extensions, hooks,
  precedence, diagnostics, reload, and shutdown from a real project `.tau` directory.
- Verify there is no second Main Sequence configuration path and no deployment flag required to
  trust extensions from the verified workspace.
- Test broken and malicious project extension failure modes within the documented shared trust
  boundary.

Exit gate: the single Tau configuration model is covered by deterministic project fixtures.

#### Phase T4: Test packaging and clean-project installation

- Build wheel and source distributions, inspect their contents, and install them into a clean
  environment and a minimal reference repository.
- Verify packaged defaults, console scripts, dependency metadata, version reporting, public
  imports, and absence of Docker/deployment-only assets.
- Start `ms-tau` from the repository root and exercise readiness without this source tree on
  `PYTHONPATH`.

Exit gate: the published artifacts are sufficient to run the service from a normal project.

#### Phase T5: Test direct project-image deployment

- Build exactly one project image from the reference repository and its lock.
- Verify runtime credential injection/exchange, backend bootstrap, provider hydration, MCP,
  durable and sessionless execution, live streaming, persistence, cancellation, eviction, and
  shutdown.
- Assert that no Astro image, bundle, overlay Dockerfile, second build, or hidden wheel injection is
  observed.
- Run equivalent contract tests through Django deployment orchestration.

Exit gate: the one-image deployment passes the complete cross-repository end-to-end suite.

#### Phase T6: Remove legacy tests and set the new quality baseline

- Delete tests and fixtures for standalone Astro images, executor bundles, overlay builds,
  optional/no-workspace deployments, old package/import/CLI/settings compatibility, and both
  retired roles after their migration checks expire.
- Remove temporary comparison tests after cutover rather than maintaining two architectures.
- Establish new coverage, mutation/static-analysis, packaging, protocol-fixture, and end-to-end
  gates based on the SDK's public risk surface.
- Verify that remaining references to Astro exist only in explicit migration or historical tests.

Exit gate: the Main Sequence TAU SDK test suite describes only the supported new project and fails
if the retired ontology is reintroduced.

### Cross-phase controls

- Every code phase adds or updates unit, integration, packaging, and end-to-end tests proportional
  to the changed boundary.
- No prior ADR, source module, test, fixture, document, workflow, or operational asset is inherited
  without a recorded disposition.
- Every public behavior change updates documentation and examples in the same phase; future-state
  docs remain labeled until release.
- The current HTTP and streaming protocol is preserved unless a separate ADR explicitly changes
  it.
- Removal tasks occur only after replacement behavior is proven and consumers are inventoried.
- No phase may reintroduce a second Tau configuration model, platform-side SDK injection, or a
  no-workspace deployment as a temporary shortcut.
- Tests that enforce eliminated Astro ontology are deleted after their bounded migration purpose;
  they are not treated as permanent regressions.
- Phase completion is recorded in this ADR or a linked implementation task with evidence for its
  exit gate.

## Verification

The architecture is complete only when all of the following are true:

- the Main Sequence TAU SDK has its own project charter, version line, public API, active ADR
  index, test strategy, documentation root, CI, release process, and operational identity;
- every prior ADR and implementation artifact has a reviewed re-adopt, amend/reissue, supersede, or
  eliminate disposition with evidence;
- the new active ADR index contains no unreviewed Astro decision marked as implicitly accepted;
- the new test suite contains the required portable contracts and rewritten SDK tests but no
  permanent assertion of the retired deployment ontology;
- a clean example CodeRepository can build one image from its own lock and launch `ms-tau`;
- that example needs no project-written FastAPI routes, Tau bootstrap, streaming, authentication,
  session-manager, or persistence glue;
- `ms-tau`, when started from the repository root with platform-injected runtime credentials,
  exposes the current health, chat, responses, sessions, and A2A route families and lazily loads a
  durable `CodingSession` for an existing backend session;
- the image contains no platform-injected `mainsequence-astro` or overlay wheelhouse;
- deployment records contain a required workspace branch/ref and verified commit;
- neither legacy agent type appears in runtime configuration, backend routing, or image catalogs;
- deleting `.tau/extensions` removes project tools and adding one makes it available without a
  platform flag;
- a project with no `.tau` overrides receives the SDK's packaged Astro/Tau defaults;
- the normal project `.tau` configuration can override Tau's general instructions and other
  supported defaults without an SDK-specific second configuration format;
- project authentication, provider resolution, durable session persistence, Main Sequence MCP,
  chat, and A2A behavior still pass end-to-end tests;
- the running service reports the actual SDK, Tau, image, commit, and `.tau` composition digests;
- a project can select a compatible newer SDK version without waiting for an Astro image release;
- an incompatible SDK version fails before serving traffic with an actionable compatibility
  error; and
- CI contains no job that publishes an Astro runtime/bundle image or builds an Astro overlay onto a
  previously built project image.

## Rejected Alternatives

### Treat this as an Astro rename or ordinary major-version refactor

Rejected. The unit of ownership changes from a platform-deployed image/runtime product to a
project-installed SDK dependency, and workspace, configuration, extension, release, and deployment
responsibilities move with it. Calling this Astro version 5 would encourage unreviewed inheritance
of old ADRs, APIs, tests, documentation, and operational assumptions. Proven implementation may be
migrated, but Main Sequence TAU SDK is governed as a new project.

### Keep the standalone orchestrator for projects that do not need a workspace

Rejected. It preserves the second topology, the no-project ambiguity, and the need for a platform
runtime image. An orchestrating agent can be a normal repository-backed project.

### Keep `code-repository-executor` as the single remaining name

Rejected. Its workspace semantics are retained, but the name describes an implementation role
rather than the product. Keeping it would also preserve backend branching that should disappear.

### Continue injecting a platform-pinned SDK wheel into project images

Rejected. The effective dependency would remain absent from the project's manifest and lock, and
the project image would still be composed by two owners. Projects already execute arbitrary code;
platform injection does not create a meaningful protection boundary.

### Publish a thin Main Sequence TAU base image containing the SDK

Rejected as the canonical deployment. It only moves the hidden dependency from an overlay into a
base tag and couples SDK upgrades to base-image selection. A neutral Python base may be offered,
but the project must explicitly declare `ms-tau-sdk`.

### Protect the SDK from `.tau/extensions` in the same container

Rejected. Extensions and ordinary project modules have the same process, filesystem, credentials,
and Unix identity. Pretending only the extension loader is untrusted is not isolation. Stronger
tenant isolation must occur at the workload/container boundary.

### Add a Main Sequence prompt configuration beside Tau's configuration

Rejected. It would create two configuration systems and ambiguous precedence. The SDK ships its
prepackaged defaults, and project overrides use Tau's general `.tau` configuration. Missing native
override capabilities belong in Tau's configuration contract.

### Maintain old and new deployment modes indefinitely

Rejected. Dual modes would retain nearly all present build, routing, testing, and monitoring
complexity and make the new dependency ownership unreliable.
