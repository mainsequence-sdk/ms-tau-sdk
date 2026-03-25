# Astro agent context

This repository is a Pi package that teaches and implements a Main Sequence project orchestration architecture.

## Core runtime flow

1. `.pi/APPEND_SYSTEM.md` provides the static parent orchestrator prompt.
2. `docs-context` appends generated repository context at `before_agent_start`.
3. `project-policy` appends child-specialist policy only when Astro spawns a child process.
4. The parent agent translates the user request into a Main Sequence project brief and uses the Main Sequence CLI to create or open the project.
5. The parent agent writes the checked-out project's `astro/` handoff files.
6. The parent agent normally delegates implementation to `mainsequence-project-coder` through `delegate_specialist` with the project's `cwd`.
7. The parent agent uses `doc-bug-auditor` for structured project status review and can still use `audit_recent_changes` for Astro-side edits.

## Specialists
- doc-bug-auditor: Reviews a Main Sequence project for status, blockers, failures, and completion | tools: read, grep, find, ls, bash
- mainsequence-project-coder: Implements tasks inside a checked-out Main Sequence project | tools: read, grep, find, ls, bash, edit, write
- rpro-builder: Builds disposable Main Sequence projects from fixed Astro guidelines | tools: read, grep, find, ls, bash, edit, write

## Registered extension tools
- docs-context: refresh_docs_index
- project-policy: (no registered tool found by simple scan)
- recent-changes: audit_recent_changes, list_recent_changes
- specialist-delegate: delegate_specialist

## External runtime packages
- pi-web-access: fetch_content, get_search_content, web_search | installed (0.10.3)

## House rules
- Prefer additive changes over core rewrites.
- Prefer extensions, specialists, prompts, skills, and shared TypeScript helpers before invasive redesign.
- Update docs and knowledge together when architecture changes.
- Run `refresh_docs_index` or `npm run docs:index` after structural changes.

## Important reading
- `docs/README.md` — Astro docs
- `docs/getting-started/pi-primer.md` — Pi primer for Astro
- `docs/getting-started/request-lifecycle.md` — Request lifecycle
- `docs/workflows/main-sequence-project-flow.md` — Main Sequence project flow
- `README.md` — Astro
