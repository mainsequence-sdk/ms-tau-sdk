# Specialist runtime

Source: [`pi/extensions/tools/specialist-delegate/runtime.ts`](../../../../pi/extensions/tools/specialist-delegate/runtime.ts)

## Purpose

Run one specialist in an isolated child `pi` process and convert the child JSON stream into structured results and live status updates for the parent tool.

## Child process model

The runtime spawns:

```bash
pi --mode json -p --no-session
```

and then layers Astro-specific environment onto it, including:

- `ASTRO_SUBAGENT_CHILD=1`
- `ASTRO_ACTIVE_SPECIALIST`
- `ASTRO_TARGET_PROJECT_ID` when present
- stored Main Sequence CLI auth environment

## Main responsibilities

- write temporary appended prompt files for child runs
- stream live assistant and tool status back to the parent
- collect final messages, usage, stderr, exit code, and stop reason
- support cancellation via `AbortSignal`
- clean up temporary prompt files after the child exits

## Main exports

- `runSingleAgent(...)`
- `getFinalOutput(messages)`
- `isResultFailure(result)`
- `SingleResult`
- `DelegateToolDetails`

## Live update behavior

The runtime derives human-readable live status from:

- assistant text events
- tool call events
- tool execution events
- tool result messages

These are kept in a short `liveTrace` so the parent can show progress without needing the full child transcript.

## Related files

- [`README.md`](./README.md)
- [`agents.md`](./agents.md)
- [`../../shared/agent-registration.md`](../../shared/agent-registration.md)
