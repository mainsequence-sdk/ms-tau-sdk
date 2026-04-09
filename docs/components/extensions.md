# Extensions

Extensions are the runtime core of Astro.

They live under `pi/extensions/` and are where Astro registers hooks and tools.

## Current extensions

### `project-policy`

Purpose:

- append child-specialist policy only for delegated child processes

Why it exists:

- the parent already has a static prompt
- the child still needs runtime-only guardrails

### `specialist-delegate`

Purpose:

- register `delegate_specialist`
- discover specialists in `.pi/agents/`
- spawn child `pi` processes
- stream live child progress back to the parent

Why it exists:

- gives Astro a real subagent model without changing Pi core
- lets a child run in the checked-out project folder via `cwd`

### `recent-changes`

Purpose:

- track Astro-side file edits made by the parent session
- register `list_recent_changes`
- register `audit_recent_changes`

Why it exists:

- Astro itself sometimes needs review separate from the target project

## Extension hooks used here

The most important hook in this repo is `before_agent_start`.

Astro uses it to:

- append child-only policy for delegated specialists

## Related pages

- [`settings-and-system-prompt.md`](./settings-and-system-prompt.md)
- [`agents.md`](./agents.md)
- [`../getting-started/request-lifecycle.md`](../getting-started/request-lifecycle.md)
