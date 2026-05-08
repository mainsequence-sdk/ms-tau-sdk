# Child policy

Source: [`pi/extensions/hooks/project-policy/child-policy.md`](../../../../pi/extensions/hooks/project-policy/child-policy.md)

## Purpose

Define the runtime-only rules that every runtime-owned child process must follow.

## Current rules

- do not delegate again
- stay within the assigned role
- use the checked-out project and local instructions as primary grounding
- allow a runtime-owned child process to edit files only when the parent explicitly launched it for implementation work
- record blockers with concrete evidence
- keep output concise and actionable for the parent

## Why it is separate

The parent orchestrator has its own long-lived prompt. Runtime-owned child processes need a smaller policy layer that is injected only when Astro spawns them.

## Related files

- [`README.md`](./README.md)
