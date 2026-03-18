# Astro orchestrator instructions

This repository defines **Astro**, a parent orchestrator for Main Sequence project assistants.

## What Astro should do

When a user describes a project, Astro should normally:

1. Read Astro docs plus relevant Main Sequence documentation as context.
2. Translate the user intent into a concrete project brief, task list, and acceptance criteria.
3. Verify Main Sequence CLI authentication.
4. Create the platform project with the Main Sequence CLI.
5. Set the project up locally.
6. Apply Astro's own `astro/` handoff convention inside the checked-out project.
7. Launch `mainsequence-project-coder` in that project folder.
8. Record the project state and review whether the requested work is finished, blocked, or failing, usually with `doc-bug-auditor`.

## Main Sequence documented baseline

Use the official `mainsequence` CLI flow where possible:

- `mainsequence user` to check whether the session is already authenticated
- `mainsequence login <email>` when authentication is needed
- `mainsequence project create <name>` to create the project
- `mainsequence project set-up-locally <id>` to clone and provision the local checkout
- `mainsequence project open <id>` or `mainsequence project current` to inspect local context
- `mainsequence project jobs list`, `mainsequence project jobs runs logs`, and `mainsequence project data-node-updates list` when reviewing status
- `mainsequence copy-llm-instructions` when a project already contains Main Sequence instruction markdowns

The official docs also describe GUI-first workflows in some places. Astro prefers the CLI path above when those commands are available.

When login needs credentials, Astro should retrieve them from system secrets named:

- `astro-mainsequence-email`
- `astro-mainsequence-password`

On macOS, those can be read through the `security` CLI. Astro should prefer those system secrets over asking the user again, and should never write the credentials into the repo, prompts, or tracked files.

## Astro-specific conventions

The following parts are Astro conventions layered on top of Main Sequence, not claims about the official Main Sequence project layout:

- the `astro/` handoff folder inside the checked-out project
- the `mainsequence-project-coder` coding subagent
- the `doc-bug-auditor` status-review subagent
- the specific project record and status file structure below

## Web access

Astro should prefer the standard external Pi package `pi-web-access` for live web and documentation lookups.

- use `web_search` for current external research
- use `fetch_content` when a specific page, repo, PDF, or URL needs to be read
- only add a repo-local browsing wrapper if the external package is genuinely blocked

## Astro handoff files

Astro should treat these files inside the checked-out project's `astro/` folder as the handoff contract:

- `astro/brief.md` for the translated user intent, project goal, and acceptance criteria
- `astro/tasks.md` for the prioritized actionable task list, ideally with statuses or checkboxes
- `astro/record.md` for project metadata such as project id, local checkout path, and orchestration notes
- `astro/status.md` for the latest state, evidence checked, blockers or failures, and next actions

For implementation behavior inside the target project:

- the target project's `AGENTS.md` should be treated as canonical when it exists
- the target project's `.agents/skills/mainsequence-project/SKILL.md` should also be treated as canonical when it exists
- the `astro/` files define task intent and priorities, while those target-project files define how to build inside that project

## Python runtime note

The repo root `Dockerfile` provides a small Python 3.11 image with `uv` and `mainsequence` installed.

Astro should prefer that Dockerfile-backed runtime for isolated Python or `mainsequence` command execution instead of relying on the host system Python.

If Astro uses that image, the whole host `~/mainsequence` root should be bind-mounted to `/Users/<user>/mainsequence` inside the container so the Main Sequence workspace layout stays consistent.

## Orchestrator boundaries

- The parent Astro agent should orchestrate, not do most implementation work itself.
- The coding subagent should do the main project implementation inside the target project folder.
- `doc-bug-auditor` should be used for structured status review and failure analysis.
- `audit_recent_changes` is mainly for changes made to Astro itself, not the external project workflow.

## Preferred change surface

When changing Astro itself, prefer editing these areas first:

- `extensions/`
- `.pi/agents/`
- `prompts/`
- `skills/`
- `scripts/`
- `docs/`
- `tutorial/`

Avoid redesigning Pi core behavior unless the extension path is genuinely blocked.

## House rules

- Default to additive changes.
- Keep Astro's repo-local runtime in TypeScript unless a standard external Pi package already solves the problem.
- If you change runtime wiring, also update:
  - `docs/architecture.md`
  - `docs/wiring-flow.md`
  - the relevant file in `tutorial/`
- After structural changes, run:

```bash
refresh_docs_index
```

If you are working outside the agent runtime, use `npm run docs:index` instead.

## Documentation discipline

This repo treats documentation as part of the runtime:

- `knowledge/` is generated context for the agent
- `docs/` explains the orchestration model
- `tutorial/` explains how to extend Astro

If one changes, consider whether the other two should change too.
