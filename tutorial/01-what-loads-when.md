# What loads when

## Startup

Pi discovers `.pi/settings.json` in the repo and uses it to load the repo root package.

Pi also auto-loads `.pi/APPEND_SYSTEM.md` for the normal parent session.

That package exposes:

- `extensions/`
- `prompts/`
- `skills/`

## Before each prompt

One extension always matters immediately, and one matters only for child specialists:

### `docs-context`
Appends generated repo context to the system prompt.

### `project-policy`
Appends child-specialist policy only when Astro spawns a child process.

## During the parent run

The parent agent may call:

- `delegate_specialist`
- `list_recent_changes`
- `audit_recent_changes`
- `web_search`
- `fetch_content`
- `get_search_content`

## During the child run

The child specialist runs its own `pi` process in the same repo.

That means it also gets:
- local files
- docs context
- project policy

But it is marked as a child process through environment variables, so the child gets the child-only policy instead of parent behavior.
