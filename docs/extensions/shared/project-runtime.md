# Project runtime bootstrap

Source: [`pi/extensions/shared/project-runtime.ts`](../../../pi/extensions/shared/project-runtime.ts)

## Purpose

Bootstrap a checked-out Main Sequence project into the deterministic runtime expected by `mainsequence-project-coder`.

## Main exports

- `bootstrapProjectCoderRuntime(...)`
- `buildActivatedProjectEnv(...)`
- `formatProjectRuntimeSummary(...)`

## Bootstrap steps

`bootstrapProjectCoderRuntime(...)` runs these steps in order:

1. `sdk_status`
2. `build_local_venv`
3. `resolve_venv`
4. `activate_venv`
5. `uv_sync`
6. `read_venv_mainsequence`

## Output

On success it returns a `ProjectRuntimeSnapshot` containing:

- checked time
- project cwd
- whether `AGENTS.md` exists
- parsed SDK status
- active `.venv` paths
- detected `mainsequence` version inside the venv

On failure it returns the failed step plus stdout, stderr, exit code, and error text.

## Event callback

Callers can provide `onEvent` to observe structured `start`, `success`, and `failure` events for each bootstrap step.

## Related files

- [`../../../interface/stream/server.ts`](../../../interface/stream/server.ts)
- [`../../../.pi/agents/mainsequence-project-coder.md`](../../../.pi/agents/mainsequence-project-coder.md)
