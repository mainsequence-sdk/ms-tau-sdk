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

## Required machine-local secrets

Astro should rely on machine-local secrets for credentials instead of tracked files.

Main Sequence secrets:

- `astro-mainsequence-email`
- `astro-mainsequence-password`

GitHub issue-escalation secrets:

- `astro-github-token`
- `astro-github-user` (optional metadata)

Usage notes:

- `astro-mainsequence-*` is for `mainsequence login`
- `astro-github-token` is for searching existing issues and opening new issues in `mainsequence-sdk/mainsequence-sdk`
- `astro-github-user` is optional reporting metadata
- for the public `mainsequence-sdk` repo, source inspection or cloning often does not require GitHub auth
- on macOS, these secrets can be read with the `security` CLI
- use `security find-generic-password -a "$USER" -s astro-github-token -w` for the PAT
- use `security find-generic-password -a "$USER" -s astro-github-user -w` only if user metadata is needed
- use the retrieved PAT directly in the REST request or place it only in a short-lived local shell variable such as `ASTRO_GITHUB_TOKEN`
- do not rely on unrelated GitHub auth state when the Astro GitHub secrets are missing
- do not look for or use any GitHub credentials outside `astro-github-token` and optional `astro-github-user`
- do not use generic GitHub environment variables like `GH_TOKEN` or `GITHUB_TOKEN` as an auth source for issue escalation
- prefer GitHub REST API over `gh` for duplicate-issue search and issue creation
- typical REST path:
  - `GET https://api.github.com/search/issues` for duplicate search
  - `POST https://api.github.com/repos/mainsequence-sdk/mainsequence-sdk/issues` for issue creation

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
    - exact command or action attempted
    - working directory or target path when relevant
    - exit code if known
    - traceback, stderr excerpt, or log snippet
    - concrete identifiers such as failing file, job id, run id, or URL when available
    - what was already tried and the next recovery step
  - next actions

## Status review structure

When Astro or `doc-bug-auditor` reviews project progress, the output should use this structure:

1. Overall state: `finished`, `in_progress`, `blocked`, or `failed`
2. Completed work
3. Open tasks
4. Blockers or failure causes, including command and traceback-style evidence when available
5. Upstream `mainsequence-sdk` assessment: `not_involved`, `possible`, `likely`, or `confirmed`
6. Evidence checked
7. GitHub issue status: `not_needed`, `existing_issue_found`, `issue_opened`, or `drafted`
8. Recommended next actions

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
  - determines whether a failure looks like an upstream `mainsequence-sdk` execution issue
  - inspects the public `mainsequence-sdk` repository when needed
  - does not need a second user confirmation before opening an upstream issue once the escalation rules are satisfied
  - searches for duplicates and opens a GitHub issue through REST API when warranted

## Python runtime note

The repo root `Dockerfile` provides a small Python 3.11 image with `uv` and `mainsequence` installed.

That is meant to give the agent an isolated Python runtime for Main Sequence work without forcing the host machine to provide the same Python environment.

To make checked-out host projects visible inside that container, the whole host `~/mainsequence` root still has to be bind-mounted at runtime, for example to `/Users/<user>/mainsequence` inside the container.
