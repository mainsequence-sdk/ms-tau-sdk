# Extensions

Extensions are the runtime core of Astro.

They live under `pi/extensions/hooks/` and `pi/extensions/tools/` and are where Astro registers hooks and tools.

Launch scripts load `.env` and start the Main Sequence token refresh loop (when configured) before extensions run.

The Docker targets keep the same extension surface by copying `.pi/`, `pi/`, `interface/`, and
`scripts/` into the image and then starting either the normal Pi launcher or the HTTP stream launcher.

`docker-compose.yml` runs those same images and mounts only the host Pi agent directory and Main Sequence
workspace directory without changing the extension loading model.

The split is organizational:

- `hooks/` extensions focus on lifecycle hooks like `before_agent_start`
- `tools/` extensions focus on tool registration and delegation

## Current extensions

### `project-policy`

Purpose:

- append child-specialist policy only for delegated child processes

Why it exists:

- the parent already has a static prompt
- the child still needs runtime-only guardrails

### `agent-registration`

Purpose:

- look up or create a backend Agent record for each parent or child session when enabled

Why it exists:

- ensures every parent or child session can be referenced by the same backend Agent `id`
- gated by `BUILD_AGENTS_IN_BACKEND=1`

Notes:

- registration uses a deterministic user-scoped `agent_unique_id` in the form
  `{agent_name}_{user_id}`.
- `mainsequence-project-coder` also includes the project id in the form
  `{agent_name}_{user_id}_{project_id}`.
- that unique id is passed to backend `get_or_create`, and the returned Agent `id` is the value
  Astro should expose as `agent_id`.
- if the user id is missing, registration is skipped for that session.

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

Why it exists:

- Astro itself sometimes needs review separate from the target project

### `telemetry`

Purpose:

- emit structured telemetry events to stdout for streaming consumers

Why it exists:

- lets the HTTP stream interface forward structured events without parsing the raw TUI output

## Extension hooks used here

The most important hook in this repo is `before_agent_start`.

Astro uses it to:

- append child-only policy for delegated specialists

## Related pages

- [`settings-and-system-prompt.md`](./settings-and-system-prompt.md)
- [`agents.md`](./agents.md)
- [`../getting-started/request-lifecycle.md`](../getting-started/request-lifecycle.md)
