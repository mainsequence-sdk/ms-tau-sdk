# `agent-registration`

Source: [`pi/extensions/hooks/agent-registration/index.ts`](../../../pi/extensions/hooks/agent-registration/index.ts)

## Purpose

This hook is currently a no-op. Astro no longer performs `agents/get_or_create` during Pi
startup.

## Behavior

- Astro stream-owned session creation and hydration use backend `agentId` authority instead of
  Pi-time deterministic registration.
- This hook remains present only so the extension loading layout stays stable while the old
  registration path is retired.

## Related files

- [`../shared/agent-registration.md`](../shared/agent-registration.md)
- [`../../../.pi/agents/mainsequence-project-coder.md`](../../../.pi/agents/mainsequence-project-coder.md)
