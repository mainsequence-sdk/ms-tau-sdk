# `agent-registration`

Source: [`pi/extensions/hooks/agent-registration/index.ts`](../../../pi/extensions/hooks/agent-registration/index.ts)

## Purpose

Register every Astro parent or child session against the Main Sequence backend when deterministic backend agent registration is enabled.

## Activation

- Hook: `session_start`
- Enabled only when `BUILD_AGENTS_IN_BACKEND` is truthy via `shouldRegisterAgents()`

## Behavior

1. Detects whether the current process is a parent orchestrator or a delegated child specialist.
2. Resolves the effective agent name from `ASTRO_ACTIVE_SPECIALIST` or falls back to `astro-orchestrator`.
3. Calls [`registerMainsequenceAgent`](../shared/agent-registration.md) with:
   - `agentName`
   - `agentRole`
   - `cwd`
   - `ASTRO_MAINSEQUENCE_USER_ID`
4. Fails the process hard if deterministic registration does not succeed.

## Environment

- `BUILD_AGENTS_IN_BACKEND`
- `ASTRO_SUBAGENT_CHILD`
- `ASTRO_ACTIVE_SPECIALIST`
- `ASTRO_MAINSEQUENCE_USER_ID`

## Failure semantics

Registration is required, not best-effort. If the shared registration helper returns no backend agent id, the hook logs the error and exits the process.

## Related files

- [`../shared/agent-registration.md`](../shared/agent-registration.md)
- [`../../../.pi/agents/mainsequence-project-coder.md`](../../../.pi/agents/mainsequence-project-coder.md)
