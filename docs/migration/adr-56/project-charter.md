# Main Sequence TAU SDK Project Charter

Status: Initial charter for ADR 56 Phase C0

Date: 2026-09-16

## Identity

**Main Sequence TAU SDK** is a new Python library project. Its distribution is `ms-tau-sdk`, its
import namespace is `ms_tau_sdk`, and its service entrypoint is `ms-tau`.

Astro is the retired source product. Proven Astro implementation may be migrated, but Astro's
image-product ontology, version sequence, public imports, command names, role identities, and
accepted-decision set are not inherited automatically.

The new project starts its own semantic-version line. Pre-stable migration releases may use `0.x`
or release-candidate versions; the first declared stable SDK contract is `1.0.0`. Astro `4.x` does
not become SDK `5.x`.

## Mission

Provide the complete, prepackaged Python primitives required to run Tau with Main Sequence from a
normal project repository, without requiring that project to implement transports, authentication,
provider hydration, session management, persistence, streaming, A2A, or Tau lifecycle glue.

## Deployment Unit

The deployment unit is one verified project image built once from one CodeRepository revision and
its dependency lock. The project declares `ms-tau-sdk` and starts `ms-tau` from the repository
workspace.

There is no Main Sequence TAU SDK runtime image, executor bundle, remote-worker overlay, generic
no-workspace service, `astro-orchestrator`, or `code-repository-executor` product role.

## What the SDK Owns

- Public Python construction primitives and the `ms-tau` service entrypoint.
- FastAPI/ASGI lifecycle and the current supported route families.
- HTTP, SSE, chat, A2A, and agent-response transport adapters.
- Runtime-credential exchange and authenticated Main Sequence backend access.
- Provider-control validation and provider-credential hydration.
- Durable session bootstrap, leases, history/snapshot restore, persistence, cancellation, eviction,
  and shutdown.
- Sessionless `AgentHarness` execution.
- Main Sequence MCP integration and protocol-required task controls.
- Packaged Tau defaults and integration with Tau's one configuration resolver.
- Runtime composition, health, readiness, logging, and observability primitives.

## What the Project Owns

- The selected and locked `ms-tau-sdk` and Tau-compatible dependency versions.
- Its source, Python dependencies, and operating-system dependencies.
- Its normal Tau `.tau` overrides, skills, prompts, extensions, hooks, and extension dependencies.
- The behavior and risk of arbitrary code it installs in the shared project/SDK workload boundary.
- Its project image, subject to Main Sequence deployment and protocol compatibility requirements.

## What the Platform Owns

- CodeRepository, branch/ref, commit, workspace, image, and service identity.
- Runtime credential issuance and scope.
- Backend authorization, session ownership, leases, provider control, and credential exchange.
- Container, network, workload, and control-plane isolation.
- Direct deployment of the already built project image and injection of secrets/configuration.
- Compatibility rejection when a project-selected SDK cannot speak the active backend protocol.

## Non-Goals

- Maintaining Astro as a second production architecture.
- Injecting or upgrading SDK wheels after the project dependency build.
- Publishing a thin SDK image that hides the dependency from the project manifest.
- Creating a second Main Sequence configuration system beside Tau's `.tau` configuration.
- Sandboxing project code from the SDK inside the same Python process and workload identity.
- Bundling optional web, media, or domain tools into the SDK.
- Preserving an old API, ADR, test, setting, or deployment artifact solely because it already
  exists.

## Founding Invariants

1. Every deployed service is bound to exactly one verified workspace revision.
2. The project explicitly declares and locks `ms-tau-sdk`.
3. One normal project build produces the final deployable image.
4. `ms-tau` runs from the repository workspace and supplies all accepted prepackaged runtime
   primitives.
5. Tau resolves one effective configuration with SDK defaults and project `.tau` overrides.
6. Runtime credentials and provider credentials are injected or hydrated, never committed.
7. Transport and persistence correctness are enforced by code, not prompt text.
8. Prior Astro artifacts require explicit disposition before migration.
9. Historical provenance may be retained, but obsolete decisions are non-normative.
10. The new project is testable from its built distributions in a clean project environment.

## Founding Public Surfaces

The exact symbol list is decided during Phase C1/C3. The required surface categories are:

- application factory;
- validated SDK settings/configuration;
- service runner used by `ms-tau`;
- durable Tau runtime/session construction;
- sessionless response execution;
- transport/router composition;
- lifecycle and shutdown hooks; and
- version and runtime-composition reporting.

Internal backend models, route helpers, persistence implementation details, and provider adapters
remain private unless a concrete embedding use case requires a stable public primitive.

## Founding ADR Policy

ADR 56 is reissued as the new project's ADR 0001. No other Astro ADR enters the active index until
the ADR disposition process explicitly re-adopts or amends it. The new active index is the only
normative architecture set.
