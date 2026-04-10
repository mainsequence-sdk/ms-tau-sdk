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
- a tutorial-verification workflow needs deterministic work inside a disposable checked-out project

Important rule:

- it should treat the target project's `AGENTS.md` and `.agents/skills/mainsequence-project/SKILL.md` as canonical when they exist

## Role split

Astro intentionally keeps a parent-child split:

- parent orchestrates
- child implements

That keeps the parent focused on Main Sequence operations and keeps specialists focused on narrower tasks.

## Related pages

- [`prompts.md`](./prompts.md)
