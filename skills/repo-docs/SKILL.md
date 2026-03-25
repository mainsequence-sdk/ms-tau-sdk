---
name: repo-docs
description: Deep instructions for navigating and updating this repository's docs and generated knowledge files.
---

Use this skill when the task depends on understanding how the repository documents itself.

## Documentation layers

- `docs/` is the canonical documentation set for architecture, components, workflows, and reference material.
- `knowledge/` is generated context for the agent.
- `AGENTS.md` gives project rules for contributors and coding agents.

## Update rule

If you change architecture, routing, extension layout, or specialist behavior:

1. update the relevant file under `docs/`
2. run `refresh_docs_index` or `npm run docs:index`

## Generated files

Treat these as generated outputs, not primary source material:

- `knowledge/docs-index.json`
- `knowledge/docs-index.md`
- `knowledge/codebase-map.md`
- `knowledge/agent-context.md`
