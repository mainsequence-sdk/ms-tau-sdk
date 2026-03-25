# Settings and system prompt

This page covers the Pi files that define Astro before any tool is called.

## `.pi/settings.json`

This is the package entry point for Pi.

It tells Pi to load:

- `extensions/`
- `prompts/`
- `skills/`
- external packages referenced from the repo, currently `npm:pi-web-access`

This file is what makes Astro a project-local Pi package rather than just a folder of markdown.

## `.pi/APPEND_SYSTEM.md`

This is the static parent prompt.

It defines Astro's main role:

- orchestrate Main Sequence projects
- use the Main Sequence CLI
- write `astro/` handoff files
- delegate implementation and review to specialists

The parent prompt is static on purpose. It is easier to inspect and reason about than generating parent policy dynamically every run.

## Child specialist policy

Child specialists should not behave like the parent.

Astro handles that with:

- `config/project-policy-specialist.md`
- `extensions/project-policy/index.ts`

The `project-policy` extension appends this child-only policy at `before_agent_start` only when Astro spawns a child process.

## Why the split exists

Astro uses a deliberate split:

- parent prompt: static in `.pi/APPEND_SYSTEM.md`
- child guardrails: runtime-only

That keeps the parent easy to inspect while still preventing children from recursively acting like orchestrators.

## Related pages

- [`extensions.md`](./extensions.md)
- [`../getting-started/request-lifecycle.md`](../getting-started/request-lifecycle.md)

