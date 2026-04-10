# Refactor Plan

## Goal

Make Astro's Main Sequence orchestration strict and deterministic so:

- existing-project requests always stay in the existing-project flow
- new-project requests always collect the required information before creation
- the orchestrator always owns project selection, creation, and `mainsequence project set-up-locally`
- specialists only run inside the checked-out target project directory
- tutorial-only specialists cannot be used for normal project work
- this repo stops assuming responsibility for writing project-local `astro/` contract files

## Strategy

### 1. Make the orchestrator the single owner of project setup

The orchestrator must always decide whether the request is:

- platform help
- existing project work
- new project creation
- tutorial verification

For an existing project:

- the orchestrator confirms the exact project
- the orchestrator runs `mainsequence project set-up-locally`
- the orchestrator starts a new delegated specialist session only after the checked-out local path is known

For a new project:

- the orchestrator must ask for the minimum project intake before any create step
- the orchestrator validates the name
- the orchestrator creates the project
- the orchestrator runs `mainsequence project set-up-locally`
- only then may it delegate implementation

### 2. Remove the ambiguous split between parent and coder responsibilities

`mainsequence-project-coder` should never be responsible for opening, selecting, or setting up a Main Sequence project locally.

It should assume:

- the project already exists
- the project is already checked out locally
- `cwd` already points at the checked-out target project

### 3. Fix the reusable orchestration prompt so it supports both project modes

The reusable project prompt must support two explicit branches:

- work on an existing project
- create a new project

It must not default to project creation language when the user wants to continue work on an existing project.

### 4. Remove Astro-owned `astro/` handoff requirements from this repo's runtime policy

This repo should not require writing project-local `astro/brief.md`, `astro/tasks.md`, `astro/record.md`, or `astro/status.md` as part of the default orchestration contract.

Project-local instructions and project-local guidelines should define how a checked-out project manages its own task tracking, state, and status artifacts.

The orchestrator should pass the correct `cwd` and task context.
It should not impose an Astro-specific file contract on every target project.

### 5. Enforce delegation constraints in code, not only in prompt text

The runtime must reject invalid delegation cases such as:

- implementation specialist called without a target `cwd`
- implementation specialist called with a `cwd` that still points to the Astro repo
- tutorial-only specialist used for non-tutorial work

### 6. Fix the container helper-binary mismatch

The Linux container should not reuse incompatible helper binaries from a host-mounted Pi cache when those binaries were downloaded on a different OS or architecture.

We need a container-safe strategy for tool binaries such as `rg`.

## Implementation Tasks

### A. Orchestrator ownership and flow control

- [ ] Update [`.pi/APPEND_SYSTEM.md`](/Users/jose/code/MainSequenceServerSide/astro/.pi/APPEND_SYSTEM.md) so the orchestrator is the only owner of project selection, creation, and `mainsequence project set-up-locally`.
- [ ] Remove the instruction that implies the coder may receive only a project id and perform local setup itself.
- [ ] Add an explicit branch in the parent policy for `existing project` vs `new project` vs `tutorial verification`.
- [ ] Require the orchestrator to confirm the exact existing project before any delegation.
- [ ] Require the orchestrator to know the checked-out local path before starting the child session.

### B. New-project intake requirements

- [ ] Define the minimum required intake for creating a new project.
- [ ] Update [`.pi/APPEND_SYSTEM.md`](/Users/jose/code/MainSequenceServerSide/astro/.pi/APPEND_SYSTEM.md) so the agent must ask for missing required inputs before `validate-name` or `project create`.
- [ ] Update [`pi/prompts/implement-additive.md`](/Users/jose/code/MainSequenceServerSide/astro/pi/prompts/implement-additive.md) so it supports both `create new` and `work on existing` flows.
- [ ] Add strict wording that "working on a project" means selecting an existing project, setting it up locally, and then delegating implementation.

### C. Coder specialist contract

- [ ] Update [`.pi/agents/mainsequence-project-coder.md`](/Users/jose/code/MainSequenceServerSide/astro/.pi/agents/mainsequence-project-coder.md) so it assumes the project is already checked out locally.
- [ ] Remove any ambiguity that the coder may open or set up a project itself.
- [ ] Make the coder explicitly require a valid checked-out project `cwd`.
- [ ] Keep the coder focused on implementation inside the target project only.

### D. Remove Astro-specific target-project handoff ownership

- [ ] Remove Astro-runtime instructions that require writing `astro/brief.md`, `astro/tasks.md`, `astro/record.md`, and `astro/status.md` as a default contract.
- [ ] Update [`.pi/APPEND_SYSTEM.md`](/Users/jose/code/MainSequenceServerSide/astro/.pi/APPEND_SYSTEM.md) to stop referring to generic "workflow handoff artifacts" as required Astro-owned files.
- [ ] Update [`.pi/agents/mainsequence-project-coder.md`](/Users/jose/code/MainSequenceServerSide/astro/.pi/agents/mainsequence-project-coder.md) to rely on project-local instructions and project-local skills instead of an Astro-imposed file contract.
- [ ] Update [`pi/prompts/implement-additive.md`](/Users/jose/code/MainSequenceServerSide/astro/pi/prompts/implement-additive.md) to remove the default requirement to write `astro/` files in the target project.
- [ ] Audit docs that still describe the `astro/` contract as mandatory and either remove that requirement or clearly mark it as legacy behavior.

### E. Delegation runtime enforcement

- [ ] Update [`pi/extensions/tools/specialist-delegate/index.ts`](/Users/jose/code/MainSequenceServerSide/astro/pi/extensions/tools/specialist-delegate/index.ts) to validate specialist eligibility before launch.
- [ ] Update [`pi/extensions/tools/specialist-delegate/runtime.ts`](/Users/jose/code/MainSequenceServerSide/astro/pi/extensions/tools/specialist-delegate/runtime.ts) so implementation specialists cannot silently fall back to the Astro repo when `cwd` is missing.
- [ ] Add a hard failure when `mainsequence-project-coder` is called without `cwd`.
- [ ] Add a hard failure when `cwd` resolves to the Astro repo instead of a checked-out target project.
- [ ] Validate tutorial verification through the parent workflow instead of a dedicated tutorial specialist.

### F. Remove the extra tutorial builder role

- [x] Delete [`.pi/agents/rpro-builder.md`](/Users/jose/code/MainSequenceServerSide/astro/.pi/agents/rpro-builder.md).
- [x] Move any tutorial-specific delegated work into [`pi/prompts/verify-mainsequence-tutorial.md`](/Users/jose/code/MainSequenceServerSide/astro/pi/prompts/verify-mainsequence-tutorial.md).
- [x] Use [`mainsequence-project-coder`](/Users/jose/code/MainSequenceServerSide/astro/.pi/agents/mainsequence-project-coder.md) as the only implementation specialist.
- [x] Remove `ADD_TUTORIAL_AGENT` gating and docs references tied only to the removed tutorial builder role.

### G. Reusable prompts and docs alignment

- [ ] Align [`.pi/APPEND_SYSTEM.md`](/Users/jose/code/MainSequenceServerSide/astro/.pi/APPEND_SYSTEM.md), [`pi/prompts/implement-additive.md`](/Users/jose/code/MainSequenceServerSide/astro/pi/prompts/implement-additive.md), and [`.pi/agents/mainsequence-project-coder.md`](/Users/jose/code/MainSequenceServerSide/astro/.pi/agents/mainsequence-project-coder.md) so they describe the same ownership model.
- [ ] Update docs that currently say the parent writes mandatory `astro/` files, including [`docs/getting-started/request-lifecycle.md`](/Users/jose/code/MainSequenceServerSide/astro/docs/getting-started/request-lifecycle.md).
- [x] Remove docs that describe a second tutorial builder specialist.
- [ ] Review README and getting-started docs for any remaining wording that biases normal project work toward project creation or tutorial flows.

### H. Container and helper binary fix

- [ ] Investigate where the Linux container gets `/root/.pi/agent/bin/rg` from and confirm the cross-platform cache mismatch.
- [ ] Stop sharing incompatible helper binaries between host and container.
- [ ] Choose one container-safe strategy:
- [ ] use a separate container-local Pi binary cache
- [ ] or clear and re-bootstrap helper binaries inside the container
- [ ] or mount only sessions/config and not host-downloaded binaries
- [ ] Update [`docker-compose.yml`](/Users/jose/code/MainSequenceServerSide/astro/docker-compose.yml), [`Dockerfile`](/Users/jose/code/MainSequenceServerSide/astro/Dockerfile), and startup docs to match the chosen approach.

## Suggested Order

- [ ] First fix the parent prompt and reusable project prompt.
- [ ] Then finish tightening `mainsequence-project-coder` and tutorial workflow behavior.
- [ ] Then add runtime enforcement in the delegation tool.
- [ ] Then clean up docs and remove stale `astro/` contract language.
- [ ] Finally fix the container helper-binary strategy.

## Acceptance Criteria

- [ ] Asking to work on an existing project never triggers the new-project creation path.
- [ ] Asking to create a new project requires the minimum intake before creation.
- [ ] The orchestrator always runs `mainsequence project set-up-locally` before delegation.
- [ ] `mainsequence-project-coder` never starts in the Astro repo.
- [ ] `mainsequence-project-coder` cannot be launched without a valid target `cwd`.
- [x] The extra tutorial builder role is gone and tutorial verification uses `mainsequence-project-coder`.
- [ ] The runtime and docs no longer require Astro-owned `astro/` files in target projects by default.
- [ ] Containerized runs no longer fail because of incompatible cached helper binaries such as `rg`.
