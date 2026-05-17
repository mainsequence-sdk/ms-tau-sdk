# `project-policy`

Source: [`pi/extensions/hooks/project-policy/index.ts`](../../../../pi/extensions/hooks/project-policy/index.ts)

## Purpose

Append Astro's child runtime policy to runtime-owned child processes before the child agent starts.

## Activation

- Hook: `before_agent_start`
- Runs only when `ASTRO_SUBAGENT_CHILD=1`

## Behavior

1. Resolves the repo root from the child `cwd`.
2. Reads the child-runtime policy markdown.
3. Appends that policy to the child system prompt under an `Astro child runtime policy` section.

This keeps the shared Astro prompt and the child runtime policy separate.

## Environment

- `ASTRO_SUBAGENT_CHILD`
- `ASTRO_ACTIVE_SPECIALIST`

## Policy source

- [`child-policy.md`](./child-policy.md)

## Related files

- [`../../shared/repo.md`](../../shared/repo.md)
