# Project-local Agent Prompts

Astro no longer ships a bundled `project-executor` prompt file. The shared runtime contract lives
in the active package/system prompt layers. `ASTRO_FIXED_PROJECT_CWD` decides whether the current
runtime is already attached to a prepared project. `ASTRO_FIXED_AGENT_TYPE`, when set, is only
backend/session identity metadata.

Project-local `.pi/agents/` files are reserved for real project-local specialist extensions, not for
the core project-attached runtime contract.

## What a project-local specialist prompt looks like

When a project defines an optional local specialist, each prompt is a markdown file with
frontmatter and a prompt body.

Example shape:

```md
---
name: project-reviewer
description: Reviews project-scoped work inside this repository
tools: read, grep, find, ls, bash, edit, write
---

Project-local specialist instructions here.
```

## Frontmatter fields used in Astro

- `name`
- `description`
- `tools`
- `model`

The body becomes the local specialist's appended system prompt.

Core project execution is not loaded from `.pi/agents`. It uses the shared system prompt contract
plus the runtime context. A fixed project cwd enters the project-attached branch.

## Related pages

- [`prompts.md`](./prompts.md)
