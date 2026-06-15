# Project-local Agent Prompts

Astro no longer ships a bundled `project-executor` prompt file. The shared runtime
contract lives in `.pi/APPEND_SYSTEM.md`. `ASTRO_FIXED_AGENT_TYPE=project-executor` plus
`ASTRO_FIXED_PROJECT_CWD` decides whether the current runtime is already attached to a prepared
project.

Project-local `.pi/agents/` files are reserved for real project-local specialist extensions, not for
the core executor runtime contract.

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

Core project execution is not loaded from `.pi/agents`. It uses `.pi/APPEND_SYSTEM.md` plus
`ASTRO_FIXED_AGENT_TYPE=project-executor` to enter the project-attached branch.

## Related pages

- [`prompts.md`](./prompts.md)
