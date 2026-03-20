# Documentation index

Generated at: 2026-03-19T12:03:40.086Z

## Documents
- `docs/architecture.md` — **Architecture**: This repository is a Pi package that gives you a **Main Sequence project orchestration architecture** without modifying Pi core.
- `docs/decisions.md` — **Decisions**: Implementation: - `extensions/docs-context/index.ts` - generated files in `knowledge/`
- `docs/folder-structure.md` — **Folder structure**: 
- `docs/main-sequence-orchestration.md` — **Main Sequence orchestration**: Astro is a parent orchestrator for Main Sequence project assistants.
- `docs/quickstart.md` — **Quick start**: This launcher:
- `docs/scope.md` — **Scope**: - Main Sequence project orchestration flow - actual specialist delegation wiring - generated documentation context - static parent prompt plus child-specialist policy injection...
- `docs/typescript-runtime.md` — **TypeScript runtime**: Astro's repo-local runtime is now TypeScript-only.
- `docs/wiring-flow.md` — **Wiring flow**: This is the concrete flow implemented in Astro.
- `tutorial/00-start-here.md` — **Start here**: This repository is both:
- `tutorial/01-what-loads-when.md` — **What loads when**: Pi discovers `.pi/settings.json` in the repo and uses it to load the repo root package.
- `tutorial/02-before-agent-start.md` — **`before_agent_start`**: This starter uses `before_agent_start` for two things:
- `tutorial/03-specialists-and-routing.md` — **Specialists and routing**: A specialist is a markdown file in `.pi/agents/` with frontmatter and a prompt body.
- `tutorial/04-agent-files-and-frontmatter.md` — **Agent files and frontmatter**: Each specialist is a markdown file.
- `tutorial/05-typescript-runtime.md` — **TypeScript runtime**: Pi extensions are TypeScript entry points, and Astro's remaining repo-local logic is simple enough that a second runtime would just add friction.
- `prompts/design-and-audit.md` — **review-main-sequence-project**: Review a checked-out Main Sequence project and report whether work is finished, blocked, or failing.
- `prompts/implement-additive.md` — **orchestrate-main-sequence-project**: Create or update a Main Sequence project, prepare Astro handoff files, and launch the coding subagent.
- `skills/extension-builder/SKILL.md` — **extension-builder**: How to add a new extension, specialist, prompt, or shared TypeScript helper in this starter repository.
- `skills/repo-docs/SKILL.md` — **repo-docs**: Deep instructions for navigating and updating this repository's docs, tutorial, and generated knowledge files.
- `.pi/agents/doc-bug-auditor.md` — **doc-bug-auditor**: Reviews a Main Sequence project for status, blockers, failures, and completion
- `.pi/agents/mainsequence-project-coder.md` — **mainsequence-project-coder**: Implements tasks inside a checked-out Main Sequence project
- `README.md` — **Astro**: Astro is a parent orchestrator for Main Sequence project assistants. It turns user intent into a concrete Main Sequence project, prepares handoff files in the checked-out projec...
- `AGENTS.md` — **Astro orchestrator instructions**: This repository defines **Astro**, a parent orchestrator for Main Sequence project assistants.

## Specialists
- `doc-bug-auditor` — Reviews a Main Sequence project for status, blockers, failures, and completion | tools: read, grep, find, ls, bash | model: (default)
- `mainsequence-project-coder` — Implements tasks inside a checked-out Main Sequence project | tools: read, grep, find, ls, bash, edit, write | model: (default)

## Extensions and tools
- `docs-context` — tools: refresh_docs_index
- `project-policy` — tools: (no registered tool found by simple scan)
- `recent-changes` — tools: audit_recent_changes, list_recent_changes
- `specialist-delegate` — tools: delegate_specialist

## External runtime packages
- `pi-web-access` — Web search, URL fetching, GitHub repo cloning, PDF extraction, YouTube video understanding, and local video analysis for Pi coding agent | tools: fetch_content, get_search_content, web_search | installed (0.10.3)
