# `switch_project_session`

Source: [`pi/extensions/tools/switch-project-session/index.ts`](../../../pi/extensions/tools/switch-project-session/index.ts)

## Purpose

Request a real handoff from the parent orchestrator into a project-scoped `mainsequence-project-coder` session.

## When to use it

Use this after Astro has:

- selected the target Main Sequence project id
- checked the project out locally
- resolved the absolute project `cwd`

## Parameters

- `projectId`
  - required selected Main Sequence project id
- `cwd`
  - required absolute path to the checked-out project
- `initialTask`
  - optional concrete project-local work to carry into the coder session
- `summary`
  - optional short summary of the established project context

## Validation

The tool rejects the request when:

- `projectId` is empty
- `cwd` is empty
- `cwd` does not resolve to an existing directory

## Output

On success, the tool returns `details.sessionSwitch` with:

- `kind: "project_session_switch"`
- `agentName: "mainsequence-project-coder"`
- `projectId`
- `cwd`
- `initialTask`
- `summary`

This structured payload is consumed by Astro's stream runtime to create the new coder session.

## Related files

- [`../../prompts/implement-additive.md`](../../prompts/implement-additive.md)
- [`../../interface/sessions.md`](../../interface/sessions.md)
