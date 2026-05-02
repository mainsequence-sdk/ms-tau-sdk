# ADR: Backend-Mediated Project Executor Runtimes

## Status

Proposed

## Context

Astro now has two distinct concerns that should not share the same runtime topology:

- the user-facing `astro-orchestrator`
- the project-scoped `mainsequence-project-executor`

The orchestrator is a conversational control-plane runtime. The executor is a project-specific
execution runtime that should run against a prepared project environment.

This ADR does **not** replace Astro's existing orchestrator behavior for normal project work.
Existing orchestrator-led flows may still use the current project-builder or project-coder
specialist model where appropriate.

Those roles have been drifting together in the local Astro implementation. In particular, the
executor has started to look like a normal Astro specialist that the orchestrator can route to
directly.

That is the wrong architecture for this capability.

The desired system is:

- the orchestrator stays user-facing
- the executor stays runtime-isolated
- the backend owns executor lifecycle and message routing
- local development uses the same backend-mediated contract as production

This ADR exists to make that boundary explicit before more executor-specific behavior is added to
Astro's normal specialist/delegation flow.

## Problem

The current gap has two parts.

### 1. Remote executor runtime

In production, a project executor should run in its own pod or container with a project-specific
runtime. That runtime may come from:

- a project image that already contains the project code and dependencies
- an image launcher chosen by the backend

That is not the same thing as spawning a local child `pi` process from inside the orchestrator pod.

### 2. Local developer workflow

The remote image path is correct for deployment, but it is awkward for local debugging when the
developer already has a real project checkout on disk, for example:

```text
/Users/jose/mainsequence-dev/main-sequence-workbench/projects/hope-30-81
```

For local development, the developer wants to mount an existing project folder into an executor
container and run the executor against that mounted project.

That local loop should still preserve the same architectural rule as production:

- the orchestrator and executor do not know about each other directly
- the backend is the only control plane between them

## Decision

Astro will treat `mainsequence-project-executor` as a backend-mediated runtime, not as a normal
Astro specialist.

Astro will also treat `mainsequence-project-executor` as a **launch modality** of the Astro
runtime itself.

That means:

- in the normal mode, Astro starts with `astro-orchestrator` as the main agent
- in executor mode, Astro starts with `mainsequence-project-executor` as the main agent

This ADR does not remove or redefine the orchestrator's existing ability to route normal
project-building work through the current specialist flows. It only states that
`mainsequence-project-executor` is not one of those normal orchestrator-specialist targets.

The architecture will have three layers:

1. `astro-orchestrator`
   - user-facing
   - talks only to backend APIs
   - keeps its existing orchestrator behavior for normal specialist flows
   - never directly launches, switches into, or specialist-delegates to
     `mainsequence-project-executor`
2. backend executor control plane
   - owns session allocation, executor lifecycle, and message routing
   - chooses whether the executor runs remotely or locally
3. `mainsequence-project-executor`
   - runs as a separate runtime
   - serves executor requests for one prepared project environment
   - does not participate in normal orchestrator specialist routing

## Remote Runtime Contract

The production or deployed executor path will continue to use a dedicated image-backed worker
runtime.

That runtime is represented by:

- `Dockerfile.remote-worker`
- a project-specific image or project-specific image overlay

In that path:

- Astro is launched directly in executor mode
- `mainsequence-project-executor` is the main runtime agent, not `astro-orchestrator`
- Astro runtime files live under `/app`
- the project runtime lives at the project image's configured project root
- the backend launches the executor container or pod
- the backend passes the executor session metadata and runtime credentials

The executor image is a deployment artifact, not the primary local-debugging loop.

## Local Runtime Contract

Local development will use a separate executor harness image:

- `Dockerfile.remote-worker.local`

This local image exists specifically to make executor debugging practical when a developer already
has a project checkout on the host machine.

### Local image shape

`Dockerfile.remote-worker.local` will:

- start from `python:3.11-slim`
- install the Astro runtime
- install the minimum container dependencies needed to bootstrap and run a mounted project
- avoid baking a specific project checkout into the image
- launch Astro directly in executor mode, with `mainsequence-project-executor` as the main agent

### Local project mount

The normal local `docker-compose.yml` stack will include an executor service that accepts a host
env var such as:

```text
A2A_DEV_PROJECT=/Users/jose/mainsequence-dev/main-sequence-workbench/projects/hope-30-81
```

That path will be mounted into a fixed container path:

```text
/workspace/project
```

The local executor runtime will then use:

- `ASTRO_FIXED_AGENT_NAME=mainsequence-project-executor`
- `ASTRO_FIXED_PROJECT_CWD=/workspace/project`
- `ASTRO_EXECUTION_MODE=remote_project_worker`

The fixed container path is important because the runtime contract should not depend on host path
shapes.

## Local Mounted-Project Rule

The local executor harness assumes the mounted host project directory is already the project root
that `mainsequence-project-executor` should work against.

In that local mode:

- Astro runtime still lives in the container under `/app`
- the project is mounted as-is into `/workspace/project`
- the executor uses `/workspace/project` as its fixed project cwd
- the local harness does not clone or materialize a second copy of the project

Any project-specific runtime behavior needed by the executor must already be visible from that
mounted project path and the local container environment.

## Runtime Boundary Rules

These rules are mandatory.

### Orchestrator rules

`astro-orchestrator`:

- keeps its existing specialist-routing behavior for non-executor workflows
- must not directly spawn `mainsequence-project-executor`
- must not treat the executor as a normal `delegate_specialist` target
- must not use `switch_project_session` to enter the executor runtime
- must not rely on repo-local executor-only startup assumptions

The orchestrator may only call backend APIs that represent executor operations.

### Executor rules

`mainsequence-project-executor`:

- runs as its own runtime
- may use Astro's stream runtime internals for its own request handling
- must not be surfaced as a normal local specialist of the orchestrator

### Backend rules

The backend:

- is the only component that knows how to locate or launch the executor runtime
- decides whether the executor is a Kubernetes pod or a local Docker container
- owns message delivery between orchestrator-facing flows and executor-facing flows

## API Direction

The intended A2A direction is:

1. orchestrator calls backend API
2. backend starts or reuses an executor runtime
3. backend sends work to the executor
4. backend returns status and results to the orchestrator

The orchestrator does not address executor pods or containers directly.

This is intentionally different from the current in-process orchestrator-to-specialist flows used
for existing project-builder or project-coder behavior.

This ADR intentionally prefers a backend mailbox or control-plane contract over direct runtime
discovery between Astro processes.

## Astro Streamer A2A Surface

The executor-specific A2A receiver belongs to the Astro streamer itself.

This ADR adds a dedicated A2A route family on the executor runtime:

- `POST /api/a2a/chat`
- `POST /api/a2a/cancel`

These routes are intentionally separate from the current human-facing stream contract:

- `POST /api/chat` remains the human/UI-oriented chat surface
- `POST /api/a2a/chat` becomes the machine-oriented executor surface

The executor deployment is already a specific Astro runtime launched in
`mainsequence-project-executor` mode, so the route family does **not** need a `/runs/:id`
resource layer.

The backend already knows which executor deployment it is calling. The pod does not need to be
resource-addressable by an additional Astro-local run id just to receive A2A work.

### `POST /api/a2a/chat`

`POST /api/a2a/chat` is the primary A2A receiver endpoint for executor deployments.

It should:

- accept backend-to-executor chat or task input
- return the executor output as the response stream for that same request
- stay on the Astro streamer, not on a separate sidecar or helper service

Most importantly, this endpoint must deterministically inject execution context that makes the
interaction unambiguously A2A.

At minimum, the executor runtime should always know:

- this request is agent-to-agent, not human-to-agent
- the caller is another system component acting through the backend control plane
- the response must follow the requested machine-facing response format

That context must not depend on the backend remembering to phrase the user-visible prompt in a
special way. It belongs to the executor runtime contract itself.

In practice, `POST /api/a2a/chat` should inject runtime-owned instructions equivalent to:

- this is an A2A communication
- do not answer as if speaking to an end user
- produce output in the requested response format
- treat the response format as a hard contract, not a stylistic preference

The exact request schema can evolve, but it should include:

- the backend session or conversation identity
- the caller-provided task or message payload
- the requested response format or output schema
- any project-scoped runtime metadata needed by the executor

### `POST /api/a2a/cancel`

`POST /api/a2a/cancel` is the out-of-band control route for stopping an active executor run.

This route exists separately because the A2A chat response is streamed from server to caller on the
`POST /api/a2a/chat` connection. Cancellation is a second control action, not part of that
one-way response stream.

Disconnecting the response stream is not the same thing as recording an intentional cancellation in
the executor runtime. The backend still needs an explicit way to tell Astro:

- stop the active A2A execution
- persist the cancellation state correctly
- propagate cancellation to the active Pi run

So the correct split is:

- `POST /api/a2a/chat` for streamed executor work
- `POST /api/a2a/cancel` for out-of-band cancellation control

## Minimal Prompt-Layer A2A Contract

The A2A transport alone is not sufficient. Astro's agent directives also need a minimal, explicit
rule that A2A communication is an allowed collaboration modality.

This should be implemented in the least intrusive way possible.

The goal is **not** to redefine the roles of the orchestrator, project-coder, or
project-executor. The goal is only to let each of them do one additional thing within their
existing role:

- answer directly
- or ask another agent for bounded help through A2A without changing session

That means A2A should be treated as a communication mechanism, not as a new agent role and not as
a replacement for existing handoff/session-switch behavior.

### Scope of the prompt change

The minimal directive change should apply to:

- the shared parent prompt for `astro-orchestrator`
- the `mainsequence-project-coder` specialist prompt
- the `mainsequence-project-executor` specialist prompt
- the child-specialist runtime policy that currently forbids recursive delegation

### What those directives should say

All three agent roles should be able to follow a small shared rule set:

- you may answer directly within your role
- you may request bounded help from another agent through A2A when the runtime supports it
- A2A does not imply a session switch
- A2A does not expand your role or allowed scope
- when a request is marked as A2A, respond as agent-to-agent rather than user-to-agent
- when an A2A request specifies a response format, follow that format as a hard contract

### Why this should stay minimal

The streamer already owns the deterministic A2A-context injection for `POST /api/a2a/chat`.

So the prompt layer does not need to restate transport details, backend routing, or endpoint
semantics in a heavy way. It only needs to make the agents recognize A2A as an allowed modality and
respect the machine-facing response contract.

This keeps the directive change small and avoids destabilizing the existing project workflow,
specialist routing, or session-switch behavior.

## Compose And Local Launching

Local development should use one executor flow only.

That flow is:

- the backend chooses a local Docker launcher instead of a Kubernetes launcher
- the normal `docker-compose.yml` stack includes the local executor service
- that executor service uses `Dockerfile.remote-worker.local`
- the project host path is mounted into `/workspace/project`
- the executor exposes the same API-facing behavior expected by the backend

This is not a second orchestration model. It is the local implementation of the same backend-owned
executor lifecycle.

## Pre-Backend Test Readiness

The backend executor-control contract is still required for the final deployed architecture, but it
is **not** a blocker for local executor testing.

Astro should be considered ready for local pre-backend testing when all of the following are true:

- the local executor container can be launched from `Dockerfile.remote-worker.local`
- the local executor container is available from the normal `docker-compose.yml` stack
- the mounted host project is visible at `/workspace/project`
- the executor starts in `mainsequence-project-executor` mode
- `POST /api/a2a/chat` and `POST /api/a2a/cancel` behave correctly on the executor runtime
- deterministic A2A context injection is active
- local A2A discovery and communication can be mocked without a backend round trip

In that phase:

- production backend discovery and routing may still be `TBD`
- the local test harness may use mocked discovery results
- local A2A communication may go directly to the dev executor container

This keeps the architectural direction intact while allowing executor runtime behavior to be tested
before the backend control-plane implementation is finished.

## Consequences

### Positive

- the orchestrator and executor stay cleanly separated
- local debugging becomes practical without rebuilding a project image for every iteration
- production and local flows keep the same control-plane shape
- the backend remains the only authority for executor lifecycle and routing
- executor runtime assumptions become easier to document and verify

### Negative

- Astro must maintain two executor packaging paths:
  - image-backed remote worker
  - mounted-project local worker
- some current executor wiring inside specialist/delegation flows will need to be removed or
  corrected
- local executor behavior now depends on the mounted project path being the intended executor root

## Required Corrections To Current Direction

This ADR means `mainsequence-project-executor` should not be modeled as a normal repo-local
specialist in Astro's parent-agent flow.

This does **not** mean removing every executor reference from Astro.

The executor still needs to remain a valid runtime agent for:

- direct executor-mode startup
- executor prompt loading
- executor-specific streamer request handling
- deterministic backend registration for executor sessions

The correction is narrower:

- the parent-oriented specialist discovery and delegation surfaces must stop surfacing the executor
- the local child-specialist spawn path must stop launching the executor
- parent prompts and docs must stop teaching executor access through `delegate_specialist`

In particular, executor-specific behavior should be removed or kept out of:

- specialist delegation routing
- specialist-specific local spawn paths
- parent-agent specialist discovery semantics

Executor runtime env handling inside the worker runtime may remain, but the parent runtime must not
present the executor as a normal local child specialist.

### Delegation-path corrections

The remaining implementation work is primarily about making the above boundary real in the current
Astro code paths.

`mainsequence-project-executor` currently still leaks through the normal specialist surfaces in at
least these ways:

- `pi/extensions/tools/specialist-delegate/index.ts`
  - still describes `mainsequence-project-executor` as a valid `delegate_specialist` target
  - still validates executor requests as if executor were a normal checked-out child specialist
- `scripts/run_specialist.ts`
  - still accepts `mainsequence-project-executor`
  - still spawns it through the child-specialist local process path
- `pi/extensions/tools/specialist-delegate/agents.ts`
  - still exposes `.pi/agents/mainsequence-project-executor.md` through normal project specialist
    discovery
- prompt and workflow docs
  - must not imply that executor access happens through local specialist delegation

The intended end state is:

- the executor prompt file may remain in `.pi/agents` so direct executor-mode Astro startup can
  still load its system prompt
- the executor may remain in runtime-owned allowlists needed for direct startup and registration
- but normal parent specialist discovery, `delegate_specialist`, and `run_specialist` must not
  present or launch the executor

## Implementation Plan

1. Keep `Dockerfile.remote-worker` as the remote image-backed executor artifact.
2. Add `Dockerfile.remote-worker.local` for mounted-project local debugging.
3. Add the local executor service to the normal `docker-compose.yml` stack so it mounts a host path
   into
   `/workspace/project`.
4. Treat the mounted host project path as the executor's fixed local project root.
5. Add executor-specific Astro streamer endpoints:
   - `POST /api/a2a/chat`
   - `POST /api/a2a/cancel`
6. Ensure `POST /api/a2a/chat` deterministically injects A2A context and enforces the requested
   response format contract.
7. Add a minimal prompt-layer A2A directive to:
   - `.pi/APPEND_SYSTEM.md`
   - `.pi/agents/mainsequence-project-coder.md`
   - `.pi/agents/mainsequence-project-executor.md`
   - `pi/extensions/hooks/project-policy/child-policy.md`
8. Define the backend executor-control contract around those streamer routes.
9. Refactor Astro so `mainsequence-project-executor` is not part of normal orchestrator specialist
   delegation:
   - filter it out of normal parent specialist discovery
   - make `delegate_specialist` reject it as a target
   - make `run_specialist` reject it as a child-specialist launch mode
   - update prompts and docs that still imply executor access through specialist delegation
10. Keep executor-specific runtime env handling only in the worker runtime path.
11. Treat local mocked A2A discovery and direct-to-container communication as the test harness
    until the backend contract is implemented.

## Verification Plan

- confirm the orchestrator can request executor work without directly launching or switching into
  the executor runtime
- confirm the backend can launch the remote executor runtime from `Dockerfile.remote-worker`
- confirm the backend can launch the local executor runtime from
  `Dockerfile.remote-worker.local`
- confirm the local executor can run against a mounted host project at `/workspace/project`
- confirm executor runtime requests behave the same from the backend's perspective in local Docker
  and deployed pod environments
- confirm `POST /api/a2a/chat` always injects A2A runtime context even when the caller prompt does
  not mention that the request is machine-to-machine
- confirm `POST /api/a2a/chat` responses follow the requested response format contract
- confirm the orchestrator, project-coder, and project-executor prompts all allow bounded A2A
  collaboration without redefining their primary roles
- confirm the child-specialist runtime policy no longer blocks A2A collaboration while still
  preventing unrestricted recursive specialist orchestration
- confirm `POST /api/a2a/cancel` stops the active executor run without requiring a separate
  Astro-local run id
- confirm `mainsequence-project-executor` is no longer exposed as a normal orchestrator specialist
- confirm normal parent specialist discovery no longer lists `mainsequence-project-executor`
- confirm `delegate_specialist` refuses `mainsequence-project-executor` even if the prompt file is
  present locally
- confirm `run_specialist` refuses `mainsequence-project-executor` and points callers to the
  backend/A2A path instead
- confirm local executor testing works with mocked A2A discovery and direct-to-container
  communication even while backend discovery/routing remains `TBD`

## Tasks

- [x] Add `Dockerfile.remote-worker.local`.
- [x] Add local executor launch wiring in the normal `docker-compose.yml` stack that mounts a host
  path env var into `/workspace/project`.
- [x] Add `POST /api/a2a/chat` to the Astro streamer for executor deployments.
- [x] Add `POST /api/a2a/cancel` to the Astro streamer for executor deployments.
- [x] Inject deterministic A2A context and requested-response-format instructions on
  `POST /api/a2a/chat`.
- [x] Add a minimal A2A-collaboration directive to the shared parent prompt, the
  `mainsequence-project-coder` prompt, the `mainsequence-project-executor` prompt, and the
  child-specialist runtime policy.
- [x] Update docs to distinguish:
  - remote image-backed executor runtime
  - local mounted-project executor runtime
  - backend-mediated orchestrator-to-executor communication
- [x] Support local mocked A2A testing so executor runtime behavior can be exercised before backend
  discovery and routing are implemented.
- [ ] Define the backend executor-control API contract around the A2A streamer routes.
- [ ] Filter `mainsequence-project-executor` out of normal parent specialist discovery.
- [ ] Make `delegate_specialist` reject `mainsequence-project-executor` and remove executor from its
  user-facing guidance.
- [ ] Make `run_specialist` reject `mainsequence-project-executor` as a child-specialist launch
  mode.
- [ ] Update prompts and docs that still imply executor access through specialist delegation.
