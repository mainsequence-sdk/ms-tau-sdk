# `ensure_mainsequence_cli_auth`

Source: [`pi/extensions/tools/mainsequence-cli-auth/index.ts`](../../../pi/extensions/tools/mainsequence-cli-auth/index.ts)

## Purpose

Repair or refresh Astro's runtime-managed Main Sequence CLI login for the current agent session.

## When to use it

Use this tool when a `mainsequence` command fails with an auth error such as:

- `Not logged in`
- `Token is invalid`
- `Current user fetch failed`

The intended flow is:

1. a `mainsequence` command fails with an auth error
2. call `ensure_mainsequence_cli_auth` once
3. retry the blocked command once

## Parameters

- `reason`
  - optional short note about which command or auth error triggered the repair

## Behavior

The tool calls Astro's deterministic auth bootstrap in `scripts/mainsequence_runtime_auth.ts`, captures bootstrap logs, and returns a structured success or failure result.

## Output

- success text telling the agent to retry the blocked command
- `details.ok`
- `details.reason`
- `details.logs`
- `details.error` on failure

## Related files

- [`../../../scripts/mainsequence_runtime_auth.ts`](../../../scripts/mainsequence_runtime_auth.ts)
- [`../../../.pi/APPEND_SYSTEM.md`](../../../.pi/APPEND_SYSTEM.md)
- [`../../../.pi/agents/mainsequence-project-coder.md`](../../../.pi/agents/mainsequence-project-coder.md)
- [`../../prompts/verify-mainsequence-tutorial.md`](../../prompts/verify-mainsequence-tutorial.md)
