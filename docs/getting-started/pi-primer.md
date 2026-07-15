# Pi primer for Astro

This page explains the Pi concepts Astro uses, mapped directly to this repository.

## What Pi is doing here

Astro does not modify Pi core. It shapes Pi from the outside using:

- project-local settings
- system prompts
- extensions
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

This is Astro's shared always-on prompt.

It contains only the generic Astro-hosted Pi runtime contract. Platform-specific behavior,
including Main Sequence behavior during the current adapter overlay, is composed through configured
Pi packages such as `adapters/mainsequence/pi-overlay`.

### Extensions

Extensions live under `pi/extensions/hooks/` and `pi/extensions/tools/` and do two jobs:

- register custom tools
- hook into agent lifecycle events such as `before_agent_start`

### Prompt templates

Prompt templates are reusable workflows the parent can follow.

Main Sequence prompt templates are currently delivered by `adapters/mainsequence/pi-overlay`, not root `pi/`.

### Skills

Skills are deeper instruction bundles the agent can load when relevant.

Main Sequence skills are currently delivered by `adapters/mainsequence/pi-overlay` or SDK materialization, not root `pi/`.

### External packages

This repository keeps `pi-web-access` in normal `node_modules` and loads it from there.

## The key mental model

The current Main Sequence deployment composes backend identities and runtime context around Astro:

- no-project sessions can handle platform, project creation, and workspace flows
- project-attached sessions work inside the prepared project cwd
- backend `agent_type` values such as `astro-orchestrator` and `project-executor` remain session
  metadata

The composed Main Sequence package/backend behavior handles:

- intent translation
- loading deeper skills when a branch needs more detailed guidance, such as new-project intake
- Main Sequence platform and project workflows
- current-project implementation when the runtime is already project-attached

## Read next

- [`request-lifecycle.md`](./request-lifecycle.md)
- [`../components/settings-and-system-prompt.md`](../components/settings-and-system-prompt.md)
- [`../components/extensions.md`](../components/extensions.md)
