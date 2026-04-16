# `list_recent_changes`

Source: [`pi/extensions/tools/recent-changes/index.ts`](../../../pi/extensions/tools/recent-changes/index.ts)

## Purpose

Track and list files recently changed by the parent Astro session through Pi `write` and `edit` tool calls.

## Activation

- Parent-only tool
- Disabled in child specialists when `ASTRO_SUBAGENT_CHILD=1`

## Tracked events

- clears tracked state on `session_start`
- clears tracked state on `session_switch`
- clears tracked state on `session_fork`
- records successful `write` and `edit` results on `tool_result`

## Behavior

- Paths are normalized relative to the repo root when possible.
- The in-memory tracker keeps up to 50 files.
- Astro updates the UI status key `astro-changes` when UI is available.

## Parameters

- `limit`
  - optional integer from 1 to 50
  - defaults to 10

## Output

- numbered text list of tracked files
- `details.files` with the same file list

## Related files

- [`../shared/repo.md`](../shared/repo.md)
- [`../../interface/history-hydration.md`](../../interface/history-hydration.md)
