# Skills

Skills are local instruction bundles the agent can load when a task clearly matches them.

In this repo, skills are not the main runtime mechanism. They are optional deeper guidance.

## Repo-local skills

### `pi/skills/mainsequence-platform/SKILL.md`

Use when:

- helping users interact with the Main Sequence platform or CLI

### `pi/skills/mainsequence-sdk/SKILL.md`

Use when:

- answering questions about `mainsequence-sdk`

### `pi/skills/extension-builder/SKILL.md`

Use when:

- adding a new extension
- adding a new specialist
- adding a new reusable prompt

### `pi/skills/repo-docs/SKILL.md`

Use when:

- updating the documentation system
- changing the documentation layout or navigation
- keeping docs and runtime wiring in sync

## How skills fit into Astro

Astro primarily depends on:

- system prompts
- extensions
- specialists
- prompt templates

Skills are there for deeper task-specific guidance, not for the always-on architecture.

## Related pages

- [`knowledge.md`](./knowledge.md)
- [`prompts.md`](./prompts.md)
