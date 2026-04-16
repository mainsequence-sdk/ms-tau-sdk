# Agents

Astro uses project-local specialist agents defined in `.pi/agents/`.

## What a specialist file looks like

Each specialist is a markdown file with frontmatter and a prompt body.

Example shape:

```md
---
name: mainsequence-project-coder
description: Implements tasks inside a checked-out Main Sequence project
tools: read, grep, find, ls, bash, edit, write
---

Specialist instructions here.
```

## Frontmatter fields used in Astro

- `name`
- `description`
- `tools`
- `model`

The body becomes the specialist's appended system prompt.

## Current specialists

### `mainsequence-project-coder`

Use when:

- the checked-out Main Sequence project needs implementation work
- the user has already opened or selected a checked-out Main Sequence project and the session should now stay project-local
- an explicit tutorial-verification workflow needs deterministic work inside a disposable checked-out project

Important rule:

- it should treat the target project's `AGENTS.md` and `.agents/skills/mainsequence-project/SKILL.md` as canonical when they exist
- before a new project-coder session starts, the runtime should deterministically run `mainsequence project sdk-status --path . --json`, `mainsequence project build_local_venv --path .`, `uv sync`, and then activate the checked-out project's `.venv`
- when started without a concrete task, it should use that runtime bootstrap summary first, bootstrap missing `AGENTS.md` / agent skills automatically, and then establish project-local context, summarize readiness, and stay ready for the next project-local turn
- when the first turn after project handoff is only incidental chat, the onboarding flow should still run before the normal reply

## Role split

Astro intentionally keeps a parent-child split:

- parent orchestrates
- child implements

That keeps the parent focused on Main Sequence operations and keeps specialists focused on narrower tasks.

## Related pages

- [`prompts.md`](./prompts.md)
