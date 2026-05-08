# Project runtime bootstrap

## Status

Removed with the retirement of `mainsequence-project-coder`.

Astro no longer performs the old coder-specific local project bootstrap flow in the stream server.
Project implementation now belongs on `mainsequence-project-executor`, which runs inside its own
prepared project runtime instead of relying on a coder-session bootstrap step.

## Related files

- [`../../../interface/stream/server.ts`](../../../interface/stream/server.ts)
- [`../../../.pi/agents/mainsequence-project-executor.md`](../../../.pi/agents/mainsequence-project-executor.md)
