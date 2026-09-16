# ADR 56: Create Main Sequence TAU SDK as a Workspace-Bound Python Library

Status: Accepted

Date: 2026-09-16

Implementation Status: In progress — C0 through C6, A0 through A4, T0 through T5, and D0 through
D5 complete; C7 started.
Repository-owned container and image assets were removed during C0.

Owner: Main Sequence TAU SDK

## Scope Boundary

This ADR governs only this repository and the Python project created from it:

- distribution, import, and command names;
- Python source structure and public API;
- application and Tau lifecycle primitives;
- transport, authentication-client, provider, session, persistence, and MCP behavior implemented
  by this package;
- packaged defaults and project `.tau` integration;
- tests, documentation, CI, packaging, and release artifacts; and
- removal of Astro-owned container and image-publication assets.

This ADR makes no change to any server repository, server model, database schema, API endpoint,
deployment orchestrator, image builder, service type, migration, operational process, or existing
server-side ontology. Existing external HTTP contracts are dependencies consumed by the SDK. Any
change to those systems requires its own independently approved work and is not an implementation
phase or exit gate of ADR 56.

The names `astro-orchestrator` and `code-repository-executor` do not become concepts, public types,
configuration modes, or compatibility aliases in the new SDK. This statement is limited to the SDK
surface; it does not prescribe changes to systems outside this repository.

## Context

Astro currently contains valuable Python behavior but is packaged and documented as a deployable
image product. The repository historically owned Dockerfiles, Compose configuration, Kubernetes
and image-publication assets, a runtime wheelhouse lock, and an overlay recipe. Those artifacts make
the package look like the deployment unit.

The desired product is different. A project already executes arbitrary trusted project code and
owns its Python dependencies. It should declare a normal Python dependency and receive the complete
prepackaged Tau/Main Sequence integration from that dependency. There is no useful security
boundary created by injecting the same code from a separately maintained image.

The current Python implementation already contains the substance that should survive:

- FastAPI application construction and lifecycle;
- health and readiness reporting;
- Assistant UI chat and SSE encoding;
- A2A messages, tasks, streaming, cancellation, replay, and task controls;
- agent-targeted sessionless responses;
- runtime-credential exchange and authenticated Main Sequence API access;
- provider selection, validation, and credential hydration;
- durable Tau session bootstrap, leases, history and snapshot restore, persistence, settlement,
  eviction, cancellation, and shutdown;
- Main Sequence MCP discovery and tool/resource integration; and
- packaged Tau instructions and defaults.

This is not merely a transport library. These capabilities form the prepackaged SDK primitives.
What disappears is Astro as an image/deployment product, not the accepted Python functionality.

## Decision

### 1. Create a new Python project identity

The project is named **Main Sequence TAU SDK**.

Its public identities are:

| Surface | Identity |
| --- | --- |
| Python distribution | `ms-tau-sdk` |
| Import namespace | `ms_tau_sdk` |
| Process command | `ms-tau` |
| Product name | Main Sequence TAU SDK |

The initial development release is `0.1.0`. The first stable public contract will be `1.0.0`.
Astro's version sequence is not continued.

`mainsequence-astro`, the `astro` import namespace, and `astro-stream` may exist only as bounded
extraction scaffolding. They are removed before the stable SDK release and are not compatibility
promises.

### 2. The SDK is a library, not an image

This repository does not contain or publish:

- a Dockerfile or Docker build context;
- Docker Compose configuration;
- Kubernetes manifests;
- an Astro runtime image;
- an executor bundle or remote-worker overlay;
- an image-specific dependency lock;
- image verification scripts; or
- image build/publication CI.

The package publishes a wheel and source distribution. A consuming project chooses how its own
application artifact is constructed and deployed. That process is outside this ADR.

### 3. Every SDK process is workspace-bound

`ms-tau` runs from a project workspace. The effective workspace defaults to the current working
directory and can be supplied explicitly through the supported Python/settings API.

Startup fails clearly when the workspace does not exist, is not a directory, or cannot be read.
There is no generic no-workspace mode in the SDK and no runtime-role switch selecting between two
execution topologies.

Workspace binding means the SDK can consistently resolve:

- project `.tau` configuration;
- project extensions and their imports;
- repository instructions and agent metadata;
- coding-tool filesystem paths; and
- runtime-composition diagnostics.

It does not mean that the SDK owns a clone, build, container, or deployment mechanism.

### 4. Preserve the complete accepted Python behavior as SDK primitives

The SDK owns the implementation needed to construct and run the Main Sequence Tau service. A
consumer must not have to rewrite FastAPI routes, credential exchange, provider hydration, Tau
bootstrap, streaming adapters, A2A task handling, session persistence, or shutdown cleanup.

The required construction surfaces are:

- `ms_tau_sdk.create_app(...)` for application composition;
- validated SDK settings with a required workspace;
- a service runner used by the `ms-tau` command;
- durable Tau session/runtime construction;
- sessionless response execution;
- lifecycle and shutdown hooks; and
- version and effective-composition reporting.

The exact exported symbol list is established during the package refactor. Backend wire models,
provider adapters, storage implementation details, and router internals remain private unless a
real embedding use case requires a stable public abstraction.

### 5. Provide two supported ways to start the same application

A project can run the packaged process:

```bash
uv add ms-tau-sdk
uv run ms-tau
```

Or it can expose the same application through Python composition:

```python
from ms_tau_sdk import create_app

app = create_app()
```

For an ASGI server:

```bash
uv run uvicorn api.tau.main:app --host 0.0.0.0 --port 8787
```

A minimal project shim may be:

```python
from ms_tau_sdk import create_app

app = create_app()
```

Both entry paths construct the same routers, lifecycle, authentication client, provider controls,
session manager, persistence behavior, Tau defaults, and streaming encoders.

### 6. Keep the existing runtime-credential client contract

Authenticated execution continues to accept:

```text
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET
```

The SDK exchanges the pair for short-lived access credentials through the existing Main Sequence
API contract. The secret is never written into project configuration, `.tau`, logs, reports, or
diagnostics.

Unit tests that do not call external services require no real credential. Credential-bearing end-
to-end tests remain explicit and opt-in.

### 7. Use Tau's single project configuration model

The SDK ships its current Tau behavior, instructions, prompts, and required resources as packaged
defaults. A project that supplies no override receives those defaults.

The project `.tau` directory is the only customization boundary. Tau resolves one effective
configuration:

```text
ms-tau-sdk packaged defaults
  -> Tau native resolution and precedence
  -> project .tau overrides and additions
  -> one effective Tau configuration
```

This applies to the general Tau configuration, instructions, prompts, skills, hooks, extensions,
and any other configuration surface supported by the selected Tau version.

The SDK does not introduce a second Main Sequence prompt configuration, a parallel SDK manifest,
or paired project files such as an SDK-specific `SYSTEM.md` plus `APPEND_SYSTEM.md` policy. If Tau
lacks a required general override, that capability must be added to Tau rather than emulated with a
competing configuration system.

### 8. Projects own their extensions and optional tools

Extension discovery is part of normal workspace operation; there is no Astro deployment flag that
enables or disables it.

The SDK provides only:

- Tau's core coding capabilities;
- Main Sequence MCP integration; and
- protocol-required task controls.

It does not bundle optional web access, browser/search tools, YouTube/video handling, standalone
file-search helpers, runtime-information tools, or unrelated domain packages. A project can add
those through its own dependencies and `.tau` extensions.

The project is responsible for the behavior, licensing, credentials, security, and operational
effects of extensions it installs. Requests and model output do not install new extensions at
runtime.

### 9. Treat project code and the SDK as one trust boundary

The project, its dependencies, its `.tau` extensions, and `ms-tau-sdk` execute in the same Python
environment and operating-system identity. The SDK does not claim to sandbox itself from the
project that imported it.

This does not change server-side authorization or external workload isolation. It only describes
the honest in-process boundary: project code can affect the SDK process, just as any dependency can.

### 10. Observe the effective library composition

Health/readiness or diagnostics should report, without exposing secrets or prompt contents:

- installed `ms-tau-sdk` version;
- installed Tau version;
- Python version;
- resolved workspace path or safe workspace identity;
- packaged-default version;
- normalized effective `.tau` configuration/resource digest;
- project override sources;
- extension catalog names and digest; and
- effective prompt/configuration digest.

These values describe the running library composition. They are not inferred from an Astro image
tag.

## Ownership Boundaries

### SDK owns

- Python application and lifecycle construction;
- supported transport surfaces;
- client-side authentication exchange;
- provider hydration and validation;
- durable and sessionless Tau primitives;
- persistence and task-control clients;
- Main Sequence MCP integration;
- packaged Tau defaults;
- workspace configuration resolution integration;
- local process startup; and
- package diagnostics.

### Consuming project owns

- declaring and locking `ms-tau-sdk`;
- Python and operating-system dependencies;
- repository source and working directory;
- `.tau` overrides, skills, prompts, hooks, extensions, and their dependencies;
- how the project artifact is built or deployed; and
- intentional SDK upgrades.

### Outside this ADR

- server implementation and data models;
- server API changes;
- deployment orchestration and service topology;
- repository build systems and image construction;
- database migrations or existing service/session migration;
- infrastructure credentials issuance;
- traffic routing, autoscaling, monitoring, and billing; and
- modification of any repository other than this SDK repository.

## Prior Artifact Disposition

This is a new project rather than an ordinary Astro rename. Existing artifacts are not inherited
automatically.

Every prior ADR, Python module, test, fixture, resource, document, workflow, and configuration item
must receive one disposition:

- **Re-adopt** — accept an ontology-independent behavior into the SDK.
- **Amend/reissue** — preserve useful behavior but rewrite ownership and terminology.
- **Supersede/replace** — implement a new SDK decision or primitive.
- **Eliminate** — remove behavior or machinery that does not belong to the SDK.
- **Migration-only** — retain temporarily to prove the package transition, then delete.

Historical records remain available for provenance but are not automatically normative.

### Initial ADR direction

| Prior ADR | SDK disposition |
| --- | --- |
| ADR 39 | Eliminate optional-workspace and role concepts; retain no active SDK authority. |
| ADR 40 | Eliminate the former deployment-package ontology; reconsider useful Python seams during C1. |
| ADR 47 | Amend/reissue the explicit A2A response-kind contract. |
| ADR 48 | Amend/reissue persistence and settlement behavior through SDK primitives. |
| ADR 49 | Amend/reissue critical-path and dependency-decoupling behavior. |
| ADR 50 | Eliminate image/runtime-ABI ownership; extract only Python/system prerequisites useful to consumers. |
| ADR 51 | Amend/reissue provider-control consumption as an SDK client contract. |
| ADR 52 | Supersede with always-workspace-bound Tau-native `.tau` extension semantics. |
| ADR 53 | Amend/reissue the decision not to duplicate a capability registry. |
| ADR 54 | Amend/reissue A2A task lifecycle, persistence, replay, cancellation, and streaming behavior. |
| ADR 55 | Amend/reissue the lean SDK tool boundary and project ownership of optional tools. |

Older repository-local ADRs referenced by these decisions must be reviewed only for constraints on
the Python SDK. Decisions owned by other repositories are outside this ADR.

## Removal and Retention Inventory

### Remove from this repository

- `mainsequence-astro` distribution identity;
- `astro` import namespace;
- `astro-stream` command;
- Astro-specific settings, logs, metrics, and public errors;
- standalone image, executor bundle, overlay, Docker, Compose, Kubernetes, and image-publication
  artifacts;
- image-only locks, scripts, tests, and documentation;
- optional/no-workspace SDK behavior;
- `astro-orchestrator` and `code-repository-executor` SDK switches or aliases;
- project-extension enable flags;
- Astro-only prompt injection that bypasses Tau configuration resolution; and
- bundled optional tool baggage.

### Add

- `ms-tau-sdk` distribution;
- `ms_tau_sdk` import tree;
- `ms-tau` command;
- stable application factory and construction primitives;
- required-workspace validation;
- Tau-native project configuration precedence;
- clean wheel/sdist installation tests;
- public API and project-usage documentation; and
- effective-composition diagnostics.

### Retain after explicit review

- health, readiness, chat, responses, sessions, and A2A route families;
- Assistant UI and A2A streaming/event encoding;
- runtime-credential client exchange;
- provider-control client behavior;
- durable and sessionless Tau execution;
- leases, snapshots, persistence, settlement, cancellation, eviction, and cleanup;
- Main Sequence MCP integration;
- protocol-required task controls; and
- packaged Tau defaults.

## Implementation Phases

The target is a hard package cut, implemented in reviewable phases. No phase adds a second
configuration system, a Docker artifact, or an SDK no-workspace mode.

### Code phases

#### C0 — Freeze reusable SDK behavior

- Remove repository-owned container, image, and deployment artifacts.
- Record the current Python package, command, application routes, settings, resources, Tau
  construction, tools, auth client, providers, persistence, streaming, and lifecycle behavior.
- Establish a green test, lint, and type baseline.
- Add an executable HTTP operation contract.
- Give every candidate Python SDK artifact an initial disposition and owner.

Exit: reusable Python behavior is characterized; non-SDK deployment assets are absent; candidate
SDK artifacts have a proposed disposition.

#### C1 — Establish internal library boundaries

- Separate settings, application factory, CLI, routers, authentication client, provider adapter,
  runtime/session management, persistence, Tau resources, and observability into explicit modules.
- Remove unnecessary import-time side effects.
- Define construction interfaces for durable and sessionless execution.
- Preserve observable behavior through black-box contracts.

Exit: the temporary command and application factory run through the new internal boundaries with
unchanged accepted behavior.

#### C2 — Consolidate Tau configuration

- Feed SDK defaults into Tau's native resolver.
- Make project `.tau` the only override/extension surface.
- Remove the extension enable flag and Astro-only prompt composition.
- Test defaults, general overrides, instructions, skills, prompts, hooks, extensions, diagnostics,
  reload, and shutdown.

Exit: a project without overrides receives SDK defaults, while a project with native `.tau`
configuration receives the deterministic Tau-resolved result.

#### C3 — Create the new package and public API

- Create `src/ms_tau_sdk` from reviewed implementation.
- Change distribution metadata to `ms-tau-sdk` version `0.1.0`.
- Add the `ms-tau` command and public `create_app` API.
- Require a workspace in settings and default it to the repository cwd.
- Rename Astro-specific Python/settings/diagnostic terminology.
- Package all required defaults and resources.

Exit: a clean environment installs the built wheel, imports `ms_tau_sdk`, starts `ms-tau`, and
serves health without importing `astro`.

#### C4 — Prove project consumption

- Create a minimal local fixture project that declares and locks `ms-tau-sdk`.
- Add its minimal application-factory shim.
- Run `ms-tau` from the fixture workspace.
- Exercise auth-client, provider, durable, sessionless, MCP, transport, persistence, cancellation,
  and shutdown contracts without project-written integration glue.

Exit: the fixture uses only the published SDK surface and its own `.tau` configuration.

#### C5 — Remove the Astro package surface

- Remove the `astro` source tree, `mainsequence-astro` metadata, `astro-stream`, compatibility
  settings, and temporary import adapters.
- Ensure no production code imports the retired namespace.
- Retain historical references only in clearly historical/migration documentation.

Exit: built artifacts and production tests contain only the new SDK identity.

#### C6 — Publish the SDK release candidate

- Build wheel and sdist.
- Inspect contents and metadata.
- Verify installation from an artifact without the source checkout on `PYTHONPATH`.
- Publish checksums, provenance, dependency metadata, and compatibility notes through the selected
  Python package release process.

Exit: a consumer can install a pinned release candidate as a normal dependency.

#### C7 — Stable SDK release

- Complete the active ADR index, documentation, public API, compatibility policy, CI, ownership,
  and release automation.
- Run clean-room installation and the complete contract suite.
- Publish `1.0.0` only when the intended stable surface is documented and tested.

Exit: Main Sequence TAU SDK is the only active product identity in this repository.

### Architecture-decision phases

#### A0 — Inventory repository-local decisions

Map every active and referenced historical ADR to the Python code, tests, settings, resources, and
documentation it governs. Assign an owner and proposed disposition.

#### A1 — Re-adopt ontology-independent contracts

Reissue protocol, authentication-client, provider, persistence, A2A, lease, snapshot, and streaming
decisions that remain valid without Astro images, optional workspaces, or legacy SDK roles.

#### A2 — Amend ownership-changing decisions

Rewrite decisions where responsibility moves from an Astro image/global setting into the SDK,
project dependency manifest, workspace, or Tau configuration.

#### A3 — Eliminate obsolete decisions

Mark image, bundle, overlay, no-workspace, legacy-role, secondary-configuration, and optional-tool
baggage decisions as superseded or eliminated.

#### A4 — Establish the new active ADR index

Reissue ADR 56 as the founding SDK decision and include only reviewed new-project decisions.

### Test phases

#### T0 — Inventory and classify

Map every test and fixture in this repository to the behavior or retired artifact it verifies.
Classify it as portable contract, rewritten SDK test, migration-only test, or deleted legacy test.

#### T1 — Preserve black-box behavior

Capture HTTP/OpenAPI, chat streaming, A2A, sessionless response, auth-client, provider, lease,
persistence, snapshot, cancellation, and shutdown behavior without relying on Astro internals.

#### T2 — Build the SDK unit suite

Test the new public API and intentional internal boundaries with no production import of `astro`.

#### T3 — Test Tau project configuration

Use real workspace fixtures to test defaults and `.tau` overrides, skills, prompts, hooks,
extensions, precedence, diagnostics, reload, and shutdown.

#### T4 — Test built distributions

Inspect wheel/sdist contents and run clean-install CLI, application, resource, and lifecycle tests.

#### T5 — Remove migration tests

Delete bounded compatibility tests and establish coverage/static-analysis gates based only on the
new SDK's supported surface.

### Documentation phases

#### D0 — Inventory and terminology

Classify every repository document as current behavior, historical context, target design, or
obsolete material. Give each an SDK destination or deletion rationale.

#### D1 — SDK maintainer documentation

Document public/private boundaries, construction, lifecycle, configuration, durable/sessionless
execution, transports, and test strategy.

#### D2 — Project-author documentation

Document dependency installation and locking, workspace requirements, `ms-tau`, application
composition, local credentials, `.tau` customization, and extension ownership.

#### D3 — Protocol and configuration reference

Generate or validate routes/events against tests and document settings, configuration precedence,
errors, diagnostics, and effective composition.

#### D4 — Astro-to-SDK migration guide

Provide package/import/command/settings/resource mappings and explicitly list removed image and
optional-tool behavior.

#### D5 — New-project documentation cutover

Make the SDK documentation root and active ADR index normative. Keep Astro material only in a
clearly historical archive.

## Verification

ADR 56 is complete only when:

- the distribution is `ms-tau-sdk` and imports through `ms_tau_sdk`;
- `ms-tau` starts from a required project workspace;
- `create_app` exposes the accepted health, chat, response, session, and A2A surfaces;
- the current HTTP and streaming contracts pass unless changed by a separate SDK ADR;
- runtime credentials work through the existing external API contract;
- durable and sessionless Tau execution pass independent tests;
- SDK defaults and project `.tau` overrides resolve through one Tau configuration model;
- project extensions work without an Astro enable flag;
- optional web/video/search/runtime-information tools are absent from the base package;
- wheel and sdist contain required code/resources and no container/deployment artifacts;
- a clean fixture project installs and runs the package without source-checkout leakage;
- production source and tests contain no `astro` import or `astro-stream` invocation;
- active documentation uses only the new SDK ontology; and
- no implementation step or exit gate requires modification of another repository.

## Consequences

### Benefits

- The project dependency graph visibly selects the SDK version.
- Users can inspect, extend, and override Tau behavior through normal project mechanisms.
- The repository has one purpose: a Python SDK with complete prepackaged primitives.
- Package tests replace image-composition tests.
- Optional tool baggage cannot silently enter a base image owned by this project.
- The trust boundary matches reality: arbitrary project code and imported SDK code share a process.
- The ADR can be implemented independently in this repository.

### Costs

- Projects must provide a compatible Python environment and dependency lock.
- SDK upgrades occur through normal project dependency updates.
- Broken project `.tau` configuration or extensions can break that project's SDK process.
- Removing the old package identity is a coordinated breaking change for Python consumers.
- External systems may require separate work to consume the new package, but that work is neither
  specified nor authorized by this ADR.

## Rejected Alternatives

### Keep Astro as an image and add an SDK wrapper

Rejected. It preserves two ownership models and hides which package version a project executes.

### Publish a thin SDK image

Rejected. The product is a Python dependency, not a container base image.

### Preserve no-workspace mode

Rejected. Tau coding/configuration behavior needs a concrete project workspace.

### Maintain `astro` imports and `astro-stream` indefinitely

Rejected. This is a new project identity, not an ordinary major-version rename.

### Add a Main Sequence prompt/configuration layer beside `.tau`

Rejected. Tau resolves one effective project configuration.

### Restrict project extensions to protect the SDK process

Rejected. The project already controls arbitrary code and dependencies in the same process. The
honest boundary is project responsibility, not an ineffective in-process policy flag.

### Bundle web, video, search, or convenience tools by default

Rejected. They are optional project capabilities and dependency baggage, not required SDK
primitives.

### Include changes to external server or deployment repositories

Rejected. That would mix independent ownership and release boundaries into a Python SDK ADR.
