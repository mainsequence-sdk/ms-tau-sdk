# Settings and system prompt

This page covers the Pi files that define Astro before any tool is called.

## `.pi/settings.json`

This is the package entry point for Pi.

It tells Pi to load:

- `pi/extensions/hooks/`
- `pi/extensions/tools/`
- `pi/prompts/`
- `pi/skills/`
- the repository package itself
- the repo-installed `pi-web-access` package

This file is what makes Astro a project-local Pi package rather than just a folder of markdown.

## `.pi/APPEND_SYSTEM.md`

This is the shared static Astro prompt.

It defines Astro's Main Sequence contract:

- use the Main Sequence CLI
- use runtime profile rules to distinguish project-attached and non-project-attached sessions
- handle project creation/selection when not project-attached
- work in the prepared project cwd when project-attached
- use A2A through the global A2A contract when cross-runtime communication is needed

It also defines the global Main Sequence CLI failure contract. Any failed `mainsequence ...`
command must be reported as a CLI error with the exact command, working directory when relevant,
exit code or signal, CLI version or version lookup failure, stderr, stdout, and the concrete
blocker or next action. Agents may retry auth failures once through `ensure_mainsequence_cli_auth`;
for non-auth failures they must not invent causes or retry guessed command variants.

The shared prompt is static on purpose. It is easier to inspect and reason about than generating
runtime policy dynamically every run.

## Child runtime policy

Runtime-owned child processes should not behave like the parent.

Astro handles that with:

- `pi/extensions/hooks/project-policy/index.ts`

The `project-policy` extension appends this child-only policy at `before_agent_start` only when Astro spawns a runtime-owned child process.

## Why the split exists

Astro keeps the shared prompt static and applies runtime-only child guardrails only to
runtime-owned child processes. Core project executor behavior is now part of the shared prompt
contract instead of a separate `.pi/agents` prompt file.

## Related pages

- [`extensions.md`](./extensions.md)
- [`../getting-started/request-lifecycle.md`](../getting-started/request-lifecycle.md)
