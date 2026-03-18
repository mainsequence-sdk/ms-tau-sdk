# Quick start

## 1. One-command start

```bash
npm run pi
```

This launcher:

- installs local npm dependencies when they are missing
- checks that Node 20+ is available
- verifies the `pi` CLI is installed
- installs repo-local `pi-web-access` when needed
- refreshes generated repo context
- runs `npm run check`
- starts `pi`

## 2. Manual steps, if you want them

```bash
pi install npm:pi-web-access -l
npm run docs:index
npm run check
pi
```

`npm run docs:index` refreshes:

- `knowledge/docs-index.json`
- `knowledge/docs-index.md`
- `knowledge/codebase-map.md`
- `knowledge/agent-context.md`

The agent can also do this itself through the `refresh_docs_index` tool after docs or structure changes.

`npm run check` uses `tsc --noEmit` plus the lightweight declarations in `types/pi-stubs.d.ts` so you can catch obvious TypeScript mistakes before wiring the real Pi dependencies.

Run `pi` from the repository root so project-local `.pi/settings.json` is discovered.

## Python runtime note

If the agent needs an isolated Python environment for `mainsequence`, use the repo-root `Dockerfile`. It installs Python 3.11, `uv`, and `mainsequence` without relying on the host system Python.

The image itself does not mount host folders. To expose the host Main Sequence workspace, mount the whole host `~/mainsequence` root to `/Users/$USER/mainsequence` inside the container, then point `mainsequence` at that same `/Users/<user>/mainsequence` base path.

## 3. Ask for a Main Sequence project

Examples:

- "Create a Main Sequence project to analyse Binance prices"
- "Set up a project for a dashboard that tracks FX carry strategies"
- "Review the status of my existing Main Sequence project and tell me what is blocked"

The parent agent should translate the request into a project brief, use the Main Sequence CLI, prepare the target project's `astro/` files, and then call `delegate_specialist` with `mainsequence-project-coder`.

That coding specialist should treat the target project's `AGENTS.md` and `.agents/skills/mainsequence-project/SKILL.md` as canonical when they exist.

## 4. Review status

For Astro itself:

- ask the agent to call `list_recent_changes`
- or ask it to call `audit_recent_changes`

For the checked-out Main Sequence project:

- ask the agent to call `delegate_specialist` with `doc-bug-auditor`
- or use the `review-main-sequence-project` prompt template

## 5. Learn how to extend

Read `tutorial/00-start-here.md`.
