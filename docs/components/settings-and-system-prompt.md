# Settings and system prompt

This page covers the Pi files that define Astro before any tool is called.

## `.pi/settings.json`

This is the package entry point for Pi.

It tells Pi to load Astro root runtime resources:

- `pi/extensions/hooks/agent-registration`
- `pi/extensions/hooks/session-model`
- `pi/extensions/hooks/telemetry`
- `pi/extensions/tools/runtime-info`
- the repository package itself
- the repo-installed `pi-web-access` package

Main Sequence product skills/prompts/extensions are composed separately through
`ASTRO_PI_PACKAGE_PATHS`, currently pointing at the adapter-owned
`adapters/mainsequence/pi-overlay` overlay in Main Sequence deployments.

## `.pi/APPEND_SYSTEM.md`

This is the shared static Astro Core prompt.

It defines only the generic Astro-hosted Pi runtime contract:

- Astro runs around Pi and provides deployment/runtime services.
- Pi should treat Astro runtime metadata as host-provided context.
- Pi should keep runtime-specific behavior in Pi packages or backend adapters rather than assuming
  one platform inside Astro Core.

Main Sequence-specific instructions are no longer stored in root `.pi/APPEND_SYSTEM.md`. During the
local adapter overlay they are composed from:

- `adapters/mainsequence/pi-overlay/pi/system/APPEND_SYSTEM.md`

The shared prompt is static on purpose. It is easier to inspect and reason about than generating
runtime policy dynamically every run. Package-level prompt composition is explicit and driven by
`ASTRO_PI_PACKAGE_PATHS`.

## Child runtime policy

Runtime-owned child processes should not behave like the parent.

The Main Sequence adapter overlay handles Main Sequence child-runtime policy with:

- `adapters/mainsequence/pi-overlay/pi/extensions/hooks/project-policy/index.ts`

The `project-policy` extension appends Main Sequence child-only policy at `before_agent_start`
when the host marks the process as a Main Sequence child runtime.

## Why the split exists

Astro keeps the shared prompt static and applies runtime-only child guardrails only to
runtime-owned child processes. Core Astro behavior is now part of the shared prompt contract. Main
Sequence product behavior is composed from the adapter overlay instead of living in root `.pi`.

## Related pages

- [`extensions.md`](./extensions.md)
- [`../getting-started/request-lifecycle.md`](../getting-started/request-lifecycle.md)
