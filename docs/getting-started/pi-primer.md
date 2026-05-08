# Pi primer for Astro

This page explains the Pi concepts Astro uses, mapped directly to this repository.

## What Pi is doing here

Astro does not modify Pi core. It shapes Pi from the outside using:

- project-local settings
- system prompts
- extensions
- runtime agent prompt files
- reusable prompt templates
- skills
- repo-installed Pi packages

## The main Pi pieces in this repo

### `.pi/settings.json`

This tells Pi what to load from the repository:

- local extensions
- local prompt templates
- local skills
- the repository package itself
- the repo-installed `pi-web-access` package

### `.pi/APPEND_SYSTEM.md`

This is Astro's always-on parent prompt.

It tells the default Pi session to behave as the Main Sequence orchestrator.

### Extensions

Extensions live under `pi/extensions/hooks/` and `pi/extensions/tools/` and do two jobs:

- register custom tools
- hook into agent lifecycle events such as `before_agent_start`

### Runtime agent prompts

Runtime agent prompts live in `.pi/agents/` as markdown files with frontmatter and a prompt body.

Astro currently uses:

- `mainsequence-project-executor`

### Prompt templates

Prompt templates in `pi/prompts/` are reusable workflows the parent can follow.

They are useful when a task is repeatable but does not need a new extension.

### Skills

Skills in `pi/skills/` are deeper instruction bundles the agent can load when relevant.

### External packages

This repository keeps `pi-web-access` in normal `node_modules` and loads it from there.

## The key mental model

Astro has an orchestrator-runtime structure:

- the parent session orchestrates
- the dedicated executor runtime implements when that phase is used

The parent handles:

- intent translation
- existing-project selection or new-project creation
- loading deeper skills when a branch needs more detailed guidance, such as new-project intake
- Main Sequence local setup and project preparation
- deciding when to continue orchestration versus when a later A2A executor call is needed

The dedicated executor runtime handles:

- implementation inside the dedicated project runtime

## Read next

- [`request-lifecycle.md`](./request-lifecycle.md)
- [`../components/settings-and-system-prompt.md`](../components/settings-and-system-prompt.md)
- [`../components/extensions.md`](../components/extensions.md)
