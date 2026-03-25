# Skills

Skills are local instruction bundles the agent can load when a task clearly matches them.

In this repo, skills are not the main runtime mechanism. They are optional deeper guidance.

## Repo-local skills

### `skills/extension-builder/SKILL.md`

Use when:

- adding a new extension
- adding a new specialist
- adding a new reusable prompt

### `skills/repo-docs/SKILL.md`

Use when:

- updating the documentation system
- changing generated knowledge inputs
- keeping docs and generated context in sync

## How skills fit into Astro

Astro primarily depends on:

- system prompts
- extensions
- specialists
- generated context

Skills are there for deeper task-specific guidance, not for the always-on architecture.

## Related pages

- [`knowledge.md`](./knowledge.md)
- [`prompts.md`](./prompts.md)

