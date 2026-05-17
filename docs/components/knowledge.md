# Knowledge

Astro no longer uses a generated `knowledge/` cache.

The canonical source of truth is the human-written `docs/` tree.

## Why this change

The extra generated context layer did not add enough value and often pulled in user-facing onboarding docs that should not be fed into the agent at runtime.

## What to use instead

If you need context, use:

- `docs/README.md`
- the relevant component or workflow pages under `docs/`
- `.pi/APPEND_SYSTEM.md` for the shared Astro prompt contract
- project-local `.pi/agents/*.md` only when a project defines optional specialist extensions
