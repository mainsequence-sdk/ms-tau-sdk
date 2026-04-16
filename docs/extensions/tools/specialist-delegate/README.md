# `delegate_specialist`

Source: [`pi/extensions/tools/specialist-delegate/index.ts`](../../../../pi/extensions/tools/specialist-delegate/index.ts)

## Purpose

Delegate bounded work to repo-local or user-level specialists by spawning isolated child `pi` processes.

## Modes

- single-step mode with `agent` and `task`
- chain mode with `chain[]`, where later steps can reference `{previous}`

## Core behavior

1. Discover specialists from project and or user scope.
2. Validate the request, including `cwd` and `projectId` for `mainsequence-project-coder`.
3. Optionally confirm project-local specialists in UI.
4. Spawn child runs through the runtime helper.
5. Stream partial updates and return structured per-step results.
6. Emit `delegate_start` and `delegate_end` telemetry events.

## Important guardrails

- Parent-only tool
- `mainsequence-project-coder` requires both `cwd` and `projectId`
- project-local specialists can be explicitly confirmed when UI is available

## Details payload

The tool returns `details` with:

- `mode`
- `agentScope`
- `projectAgentsDir`
- `results`

Each result entry comes from the runtime layer and includes exit status, messages, usage, and live trace data.

## Related pages

- [`agents.md`](./agents.md)
- [`runtime.md`](./runtime.md)
- [`../../hooks/project-policy/README.md`](../../hooks/project-policy/README.md)
