# Astro orchestrator instructions

This repository defines **Astro**, a parent orchestrator for Main Sequence project assistants.

## What Astro should do

When a user describes a project, Astro should normally:

1. Read relevant Main Sequence documentation plus the target project's own context as needed.
2. Translate the user intent into a concrete project brief, task list, and acceptance criteria.
3. Verify Main Sequence CLI authentication.
4. Create the platform project with the Main Sequence CLI.
5. Set the project up locally.
6. Apply Astro's own `astro/` handoff convention inside the checked-out project.
7. Launch `mainsequence-project-coder` in that project folder.
8. Record the project state and review whether the requested work is finished, blocked, or failing.

For the dedicated tutorial-regression flow, Astro should instead:

1. read the canonical upstream tutorial docs
2. derive the SDK version from the upstream repository
3. create a disposable project named `tutorial_review_[sdk_version]`
4. delegate the build step to `rpro-builder`
5. validate CLI tutorial steps first
6. validate GUI-validatable steps second with Playwright
7. raise only tutorial-documentation issues when the docs are stale or missing
8. always attempt backend cleanup before finishing

## Main Sequence documented baseline

Use the official `mainsequence` CLI flow where possible:

- `mainsequence user` to check whether the session is already authenticated
- `mainsequence login --access-token "$MAINSEQUENCE_ACCESS_TOKEN" --refresh-token "$MAINSEQUENCE_REFRESH_TOKEN" --backend "$MAINSEQUENCE_BACKEND" --projects-base "$MAINSEQUENCE_PROJECTS_BASE"` when authentication is needed
- `mainsequence project create <name>` to create the project
- `mainsequence project set-up-locally <id>` to clone and provision the local checkout
- `mainsequence project open <id>` or `mainsequence project current` to inspect local context
- `mainsequence project jobs list`, `mainsequence project jobs runs logs`, and `mainsequence project data-node-updates list` when reviewing status
- `mainsequence copy-llm-instructions` when a project already contains Main Sequence instruction markdowns

The official docs also describe GUI-first workflows in some places. Astro prefers the CLI path above when those commands are available.

Authentication must use JWTs provided in environment variables:

- `MAINSEQUENCE_ACCESS_TOKEN`
- `MAINSEQUENCE_REFRESH_TOKEN`
- `MAINSEQUENCE_BACKEND`
- `MAINSEQUENCE_PROJECTS_BASE`
- `MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS`

Astro should never request or store usernames or passwords. If authentication fails, stop and ask the user to refresh the environment variables. Do not write tokens into the repo, prompts, or tracked files.

When `MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS` is set, Astro refreshes tokens by re-running the `mainsequence login` command on that interval while the session is active.

For GitHub issue escalation:

- use a classic GitHub personal access token supplied via machine-local secrets
- treat credentials as machine-local, never repo files
- for the public `mainsequence-sdk` repo, cloning or reading source may not need GitHub auth
- do not rely on unrelated GitHub auth state when issue-escalation credentials are missing
- do not use generic GitHub environment variables like `GH_TOKEN` or `GITHUB_TOKEN` as an auth source for issue escalation
- when the issue-escalation criteria are met, Astro does not need a second user confirmation to open the upstream issue
- prefer GitHub REST API over `gh` for issue search and issue creation

## Astro-specific conventions

The following parts are Astro conventions layered on top of Main Sequence, not claims about the official Main Sequence project layout:

- the `astro/` handoff folder inside the checked-out project
- the `mainsequence-project-coder` coding subagent
- the `rpro-builder` fixed-guideline build subagent
- the specific project record and status file structure below
- the auth environment variable names and the GitHub issue escalation behavior

## Web access

The repository includes `pi-web-access` as a normal npm dependency.

Use `web_search` for current external research and `fetch_content` when a specific page, repo, PDF, or URL needs to be read.

## Astro handoff files

Astro should treat these files inside the checked-out project's `astro/` folder as the handoff contract:

- `astro/brief.md` for the translated user intent, project goal, and acceptance criteria
- `astro/tasks.md` for the prioritized actionable task list, ideally with statuses or checkboxes
- `astro/record.md` for project metadata such as project id, local checkout path, and orchestration notes
- `astro/status.md` for the latest state, evidence checked, blockers or failures, and next actions
  - when blocked or failed, include the exact command or action attempted, the working directory or target path when relevant, the exit code if known, and a traceback, stderr excerpt, or log snippet
  - include concrete identifiers such as a failing file, job id, run id, or URL when available
  - include what was already tried and the next recovery step

For implementation behavior inside the target project:

- the target project's `AGENTS.md` should be treated as canonical when it exists
- the target project's `.agents/skills/mainsequence-project/SKILL.md` should also be treated as canonical when it exists
- the `astro/` files define task intent and priorities, while those target-project files define how to build inside that project

## Python/runtime note

The repo root `Dockerfile` provides the full app runtime with Python 3.11 plus Node 20.

When using containers, run Python commands inside this same app container (do not use a separate Python-only container).
The container image installs `mainsequence` from `MAINSEQUENCE_PIP_SPEC` in the final Docker layer so
changing the library spec does not invalidate the earlier Docker layers.

If Astro uses that image, bind-mount only the exact host directories needed:

- `~/.pi/agent` to `/root/.pi/agent`
- `~/mainsequence` to `/root/mainsequence`
- `~/mainsequence-dev` to `/root/mainsequence-dev`

## Orchestrator boundaries

- The parent Astro agent should orchestrate, not do most implementation work itself.
- The coding subagent should do the main project implementation inside the target project folder.
- `rpro-builder` should be used when a project build must always follow stable shared guidelines, especially for disposable tutorial-review runs.

## Preferred change surface

When changing Astro itself, prefer editing these areas first:

- `pi/extensions/`
- `.pi/agents/`
- `pi/prompts/`
- `pi/skills/`
- `scripts/`
- `docs/`

Avoid redesigning Pi core behavior unless the extension path is genuinely blocked.

## House rules

- Default to additive changes.
- Keep Astro's repo-local runtime in TypeScript unless a standard external Pi package already solves the problem.
- If you change runtime wiring, also update:
  - `docs/getting-started/request-lifecycle.md`
  - `docs/components/extensions.md`
  - the relevant workflow or component page under `docs/`
- After structural changes, run:

```bash
npm run check
```

## Documentation discipline

This repo treats documentation as part of the runtime:

- `docs/` is the canonical human-readable documentation set for Astro

If one changes, consider whether related docs should be updated too.
