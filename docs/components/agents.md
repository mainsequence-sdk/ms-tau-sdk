# Agents

Astro uses project-local specialist agents defined in `.pi/agents/`.

## What a specialist file looks like

Each specialist is a markdown file with frontmatter and a prompt body.

Example shape:

```md
---
name: rpro-builder
description: Builds a disposable tutorial review project from fixed Astro guidelines
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

Important rule:

- it should treat the target project's `AGENTS.md` and `.agents/skills/mainsequence-project/SKILL.md` as canonical when they exist

### `rpro-builder`

Use when:

- the build should always follow stable shared guidelines
- the task is a disposable tutorial-review run

Important rule:

- it keeps its fixed guidelines embedded inside the specialist prompt
- it is only discoverable when `ADD_TUTORIAL_AGENT=1`

## Role split

Astro intentionally keeps a parent-child split:

- parent orchestrates
- child implements or builds

That keeps the parent focused on Main Sequence operations and keeps specialists focused on narrower tasks.

## Related pages

- [`prompts.md`](./prompts.md)
- [`../workflows/main-sequence-project-flow.md`](../workflows/main-sequence-project-flow.md)
