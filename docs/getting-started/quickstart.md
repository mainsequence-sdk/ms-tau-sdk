# Quickstart

## Run Astro

```bash
npm run pi
```

This launcher:

- installs local npm dependencies when needed
- checks for Node 20+
- verifies the `pi` CLI is available
- installs repo-local `pi-web-access` if needed
- runs the TypeScript check
- starts `pi`

## Run the tutorial verifier

Run Pi and use the `verify-mainsequence-tutorial` prompt template when you want the fixed tutorial-regression workflow.

## Run only `mainsequence-project-coder`

Interactive single-specialist mode:

```bash
npm run specialist -- --agent mainsequence-project-coder --cwd /absolute/path/to/checked-out-project
```

Single task mode:

```bash
npm run specialist -- --agent mainsequence-project-coder --cwd /absolute/path/to/checked-out-project "Read astro/tasks.md and implement the next task"
```

## Manual commands

```bash
pi install npm:pi-web-access -l
npm run check
pi
```

## Python and Main Sequence runtime

When Astro needs an isolated Python environment for `mainsequence`, use the repo-root `Dockerfile`.

Mount the whole host `~/mainsequence` root to `/Users/$USER/mainsequence` inside the container so Main Sequence keeps the expected workspace layout.

## Read next

- [`pi-primer.md`](./pi-primer.md)
- [`request-lifecycle.md`](./request-lifecycle.md)
- [`../workflows/main-sequence-project-flow.md`](../workflows/main-sequence-project-flow.md)
