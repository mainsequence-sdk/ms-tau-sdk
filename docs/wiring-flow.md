# Wiring flow

This is the concrete flow implemented in Astro.

## Parent request flow

### 1. Parent prompt and docs context
- `.pi/APPEND_SYSTEM.md`
  - provides the static parent orchestrator prompt
- `extensions/docs-context/index.ts`
  - loads generated context from `knowledge/`
  - appends that context to the system prompt
  - registers `refresh_docs_index` for in-agent regeneration of `knowledge/`
  - shares the same TypeScript generator used by `npm run docs:index`

### 2. Parent agent decides
The parent agent is instructed to use:

- Main Sequence docs and CLI context first
- `delegate_specialist` with `mainsequence-project-coder` for implementation in a checked-out project folder
- `delegate_specialist` with `doc-bug-auditor` for status review
- `audit_recent_changes` only when Astro itself changed
- `web_search` for fresh external information
- `fetch_content` when a specific external page or repo needs to be read

### 3. Delegate tool runs
`extensions/specialist-delegate/index.ts`:

- discovers specialist files in `.pi/agents/`
- parses frontmatter through `parseFrontmatter(...)`
- builds a child `pi` command
- passes the specialist prompt through `--append-system-prompt`
- spawns a child `pi --mode json -p --no-session ...`
- streams JSON events back into the parent tool result
- forwards partial assistant text, tool-call setup, and tool execution updates so delegation stays visible while running
- keeps a short rolling activity trail so the parent can see recent investigation phases and command snippets instead of only a generic running message

### 4. Child process behavior
The child process can run in the same repository or in a checked-out Main Sequence project via `cwd`, and it still gets:

- generated docs context
- project-local files
- child-specific project policy

But it is marked with:

- `ASTRO_SUBAGENT_CHILD=1`
- `ASTRO_ACTIVE_SPECIALIST=<name>`

The delegate runtime also passes an explicit `--append-system-prompt`, so the child does not rely on the parent's `.pi/APPEND_SYSTEM.md`.

### 5. Parent orchestration
Before delegation, the parent agent should normally:

- translate the user request into a project brief and task list
- authenticate with `mainsequence`
- create the platform project
- check it out locally
- write `astro/brief.md` with the translated intent, goal, and acceptance criteria
- write `astro/tasks.md` with the prioritized actionable task list
- write `astro/record.md` with project metadata and orchestration notes
- write `astro/status.md` with the latest state, evidence, blockers, and next actions
- expect the coding child to read the target project's `AGENTS.md` and `.agents/skills/mainsequence-project/SKILL.md` when they exist

### 6. Child implementation or review
After setup, the parent can:

- run `mainsequence-project-coder` in the checked-out project folder for implementation
- run `doc-bug-auditor` in the checked-out project folder for status review
- ask `doc-bug-auditor` to inspect likely `mainsequence-sdk` failures, inspect the public upstream repo, search for duplicate issues, and open a new issue through GitHub REST when warranted
- when issue escalation is enabled, `doc-bug-auditor` should use only `astro-github-token` and optional `astro-github-user`, and it does not need a second user confirmation once the issue-opening rules are satisfied

### 7. Astro-side audit when needed
`extensions/recent-changes/index.ts` tracks parent-session `write` and `edit` tool results in Astro itself.

That is mainly useful when you are changing Astro rather than the external project.

### 8. Final response
The parent agent returns the project id, local path, current status, and next actions.

## Why this flow is a good first iteration

- actual specialist wiring exists
- core Pi is untouched
- docs context is always present
- web access comes from a standard external Pi package instead of custom local glue
- review can be invoked as a real step
- Astro's repo-local runtime stays in TypeScript
