---
name: repo-docs
description: Deep instructions for navigating and updating this repository's docs and generated knowledge files.
---

Use this skill when the task depends on understanding how the repository documents itself.

## Documentation layers

- `docs/` is the canonical documentation set for architecture, components, workflows, and reference material.
 - `AGENTS.md` gives project rules for contributors and coding agents.

## Update rule

If you change architecture, routing, extension layout, or specialist behavior:

1. update the relevant file under `docs/`
2. run `npm run check` when you change TypeScript or runtime wiring

## Generated files

Astro no longer keeps a generated `knowledge/` cache. Treat `docs/` as the single source of truth.
