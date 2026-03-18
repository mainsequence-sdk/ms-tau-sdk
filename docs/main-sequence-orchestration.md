# Main Sequence orchestration

Astro is a parent orchestrator for Main Sequence project assistants.

Its default job is not to implement the user request directly inside Astro. Its default job is to:

1. understand the request
2. create or select the right Main Sequence project
3. prepare a clean handoff in that project's `astro/` folder
4. launch a coding subagent in the checked-out project
5. review project status and explain what finished or failed

## Main Sequence documented baseline

Astro should align with the official Main Sequence CLI workflow and prefer the `mainsequence` command over invented shell wrappers.

Core commands Astro should know:

- `mainsequence user` to verify login state
- `mainsequence login <email>` to authenticate
- `mainsequence project create <name>` to create the platform project
- `mainsequence project set-up-locally <id>` to clone the project and provision `.env`
- `mainsequence project open <id>` or `mainsequence project current` to inspect the local checkout
- `mainsequence project jobs list`
- `mainsequence project jobs runs logs <run-id>`
- `mainsequence project data-node-updates list`
- `mainsequence copy-llm-instructions` when a project already contains Main Sequence instruction markdowns

The official docs also include GUI-first flows in some places. Astro prefers the CLI path above when those commands are available.

## Astro-specific handoff layer

The `astro/` folder and its files are Astro conventions layered on top of Main Sequence. They are not presented in the official Main Sequence docs as a required project layout.

## Handoff files inside the target project

Astro should maintain these files in the checked-out project's `astro/` folder:

- `astro/brief.md`
  - translated user intent
  - project goal
  - acceptance criteria
- `astro/tasks.md`
  - prioritized task list
  - checkboxes or statuses per task
- `astro/record.md`
  - Main Sequence project id
  - platform project name
  - local checkout path
  - important orchestration notes
- `astro/status.md`
  - latest review state
  - evidence
  - blockers or failures
  - next actions

## Status review structure

When Astro or `doc-bug-auditor` reviews project progress, the output should use this structure:

1. Overall state: `finished`, `in_progress`, `blocked`, or `failed`
2. Completed work
3. Open tasks
4. Blockers or failure causes
5. Evidence checked
6. Recommended next actions

## Parent and child responsibilities

- Parent Astro agent:
  - understands intent
  - drives the Main Sequence CLI
  - prepares `astro/` files
  - chooses when to delegate or review
- `mainsequence-project-coder` child:
  - implements tasks in the target project folder
  - treats the target project's `AGENTS.md` and `.agents/skills/mainsequence-project/SKILL.md` as canonical implementation guidance when they exist
- `doc-bug-auditor` child:
  - reviews task completion, blockers, and failure causes

## Python runtime note

The repo root `Dockerfile` provides a small Python 3.11 image with `uv` and `mainsequence` installed.

That is meant to give the agent an isolated Python runtime for Main Sequence work without forcing the host machine to provide the same Python environment.

To make checked-out host projects visible inside that container, the whole host `~/mainsequence` root still has to be bind-mounted at runtime, for example to `/Users/<user>/mainsequence` inside the container.
