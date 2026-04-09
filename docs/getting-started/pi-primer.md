# Pi primer for Astro

This page explains the Pi concepts Astro uses, mapped directly to this repository.

## What Pi is doing here

Astro does not modify Pi core. It shapes Pi from the outside using:

- project-local settings
- system prompts
- extensions
- specialist prompt files
- reusable prompt templates
- skills
- external Pi packages

## The main Pi pieces in this repo

### `.pi/settings.json`

This tells Pi what to load from the repository:

- local extensions
- local prompt templates
- local skills
- external packages such as `npm:pi-web-access`

### `.pi/APPEND_SYSTEM.md`

This is Astro's always-on parent prompt.

It tells the default Pi session to behave as the Main Sequence orchestrator.

### Extensions

Extensions live under `pi/extensions/` and do two jobs:

- register custom tools
- hook into agent lifecycle events such as `before_agent_start`

### Specialists

Specialists live in `.pi/agents/` as markdown files with frontmatter and a prompt body.

Astro currently uses:

- `mainsequence-project-coder`
- `rpro-builder`
- `doc-bug-auditor`

### Prompt templates

Prompt templates in `pi/prompts/` are reusable workflows the parent can follow.

They are useful when a task is repeatable but does not need a new extension.

### Skills

Skills in `pi/skills/` are deeper instruction bundles the agent can load when relevant.

### External packages

Astro uses `npm:pi-web-access` instead of maintaining a local web wrapper.

## The key mental model

Astro has a parent-child structure:

- the parent session orchestrates
- child specialists implement or review

The parent handles:

- intent translation
- Main Sequence CLI actions
- writing the target project's `astro/` handoff files
- deciding when to delegate and when to review

The child specialists handle:

- implementation inside the checked-out project
- fixed-guideline builds
- status review and upstream bug investigation

## Read next

- [`request-lifecycle.md`](./request-lifecycle.md)
- [`../components/settings-and-system-prompt.md`](../components/settings-and-system-prompt.md)
- [`../components/extensions.md`](../components/extensions.md)
