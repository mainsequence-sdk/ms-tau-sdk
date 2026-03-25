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
appendPromptFiles: ../../config/rpro-builder-guidelines.md
---

Specialist instructions here.
```

## Frontmatter fields used in Astro

- `name`
- `description`
- `tools`
- `model`
- `appendPromptFiles`

The body becomes the specialist's appended system prompt.

If `appendPromptFiles` is present, Astro resolves those markdown files relative to the specialist file and appends them as shared guidelines.

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

- it always loads `config/rpro-builder-guidelines.md`

### `doc-bug-auditor`

Use when:

- you need structured status review
- a failure needs investigation
- you need duplicate-issue search and GitHub REST issue escalation for likely upstream SDK problems

Important rule:

- it should report concrete evidence, not vague blocker summaries

## Role split

Astro intentionally keeps a parent-child split:

- parent orchestrates
- child implements or reviews

That keeps the parent focused on Main Sequence operations and keeps specialists focused on narrower tasks.

## Related pages

- [`prompts.md`](./prompts.md)
- [`../workflows/main-sequence-project-flow.md`](../workflows/main-sequence-project-flow.md)

