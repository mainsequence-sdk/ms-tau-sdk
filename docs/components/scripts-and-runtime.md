# Scripts and runtime

Astro keeps its repo-local runtime in TypeScript.

## Repo-local runtime

Astro's runtime lives in:

- `extensions/*/index.ts`
- `extensions/shared/*`
- `scripts/refresh_docs_index.ts`
- `scripts/run_tutorial_verifier.ts`
- `scripts/start_pi.mjs`

## Why TypeScript is the default

- Pi extensions are TypeScript entry points
- Astro's local logic is mostly wiring, routing, and file shaping
- avoiding a local language bridge keeps the repo easier to inspect

## What the scripts do

### `scripts/start_pi.mjs`

Starts Astro through the local package setup.

### `scripts/refresh_docs_index.ts`

Refreshes the generated `knowledge/` files.

### `scripts/run_tutorial_verifier.ts`

Runs the fixed tutorial-verification workflow in headless mode.

## Python runtime note

The repo root `Dockerfile` is not Astro's local runtime.

It is an infrastructure helper for tasks that need:

- Python 3.11
- `uv`
- `mainsequence`

That lets Astro avoid depending on the host system Python for Main Sequence operations.

## Related pages

- [`knowledge.md`](./knowledge.md)
- [`../workflows/tutorial-verification.md`](../workflows/tutorial-verification.md)

