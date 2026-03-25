# Documentation index

Generated at: 2026-03-25T20:24:08.444Z

## Documents
- `docs/README.md` — **Astro docs**: Astro is a Pi package that acts as a parent orchestrator for Main Sequence project assistants.
- `docs/components/agents.md` — **Agents**: Astro uses project-local specialist agents defined in `.pi/agents/`.
- `docs/components/extensions.md` — **Extensions**: Extensions are the runtime core of Astro.
- `docs/components/knowledge.md` — **Knowledge**: `knowledge/` contains generated context files used by Astro.
- `docs/components/prompts.md` — **Prompts**: Prompts in `prompts/` are reusable workflow templates.
- `docs/components/scripts-and-runtime.md` — **Scripts and runtime**: Astro keeps its repo-local runtime in TypeScript.
- `docs/components/settings-and-system-prompt.md` — **Settings and system prompt**: This page covers the Pi files that define Astro before any tool is called.
- `docs/components/skills.md` — **Skills**: Skills are local instruction bundles the agent can load when a task clearly matches them.
- `docs/getting-started/pi-primer.md` — **Pi primer for Astro**: This page explains the Pi concepts Astro uses, mapped directly to this repository.
- `docs/getting-started/quickstart.md` — **Quickstart**: This launcher:
- `docs/getting-started/request-lifecycle.md` — **Request lifecycle**: This is the easiest way to understand Astro end to end.
- `docs/reference/decisions.md` — **Decisions**: Implementation:
- `docs/reference/folder-structure.md` — **Folder structure**: Project-local Pi settings, parent prompt, and specialist prompts.
- `docs/reference/scope.md` — **Scope**: - Main Sequence project orchestration - project-local specialist delegation - generated repo context - static parent prompt plus child-only policy - reusable workflow prompts -...
- `docs/reserach_guide/research_guide.md` — **research_guide**: 
- `docs/workflows/main-sequence-project-flow.md` — **Main Sequence project flow**: This is Astro's default workflow.
- `docs/workflows/tutorial-verification.md` — **Tutorial verification**: This is Astro's fixed regression workflow for the official Main Sequence tutorials.
- `prompts/design-and-audit.md` — **review-main-sequence-project**: Review a checked-out Main Sequence project and report whether work is finished, blocked, or failing.
- `prompts/implement-additive.md` — **orchestrate-main-sequence-project**: Create or update a Main Sequence project, prepare Astro handoff files, and launch the coding subagent.
- `prompts/verify-mainsequence-tutorial.md` — **verify-mainsequence-tutorial**: Verify the official Main Sequence CLI and GUI tutorials against a disposable tutorial review project.
- `skills/extension-builder/SKILL.md` — **extension-builder**: How to add a new extension, specialist, prompt, or shared TypeScript helper in this starter repository.
- `skills/repo-docs/SKILL.md` — **repo-docs**: Deep instructions for navigating and updating this repository's docs and generated knowledge files.
- `.pi/agents/doc-bug-auditor.md` — **doc-bug-auditor**: Reviews a Main Sequence project for status, blockers, failures, and completion
- `.pi/agents/mainsequence-project-coder.md` — **mainsequence-project-coder**: Implements tasks inside a checked-out Main Sequence project
- `.pi/agents/rpro-builder.md` — **rpro-builder**: Builds disposable Main Sequence projects from fixed Astro guidelines
- `README.md` — **Astro**: Astro is a Pi package that acts as a parent orchestrator for Main Sequence project assistants.
- `AGENTS.md` — **Astro orchestrator instructions**: This repository defines **Astro**, a parent orchestrator for Main Sequence project assistants.

## Specialists
- `doc-bug-auditor` — Reviews a Main Sequence project for status, blockers, failures, and completion | tools: read, grep, find, ls, bash | model: (default)
- `mainsequence-project-coder` — Implements tasks inside a checked-out Main Sequence project | tools: read, grep, find, ls, bash, edit, write | model: (default)
- `rpro-builder` — Builds disposable Main Sequence projects from fixed Astro guidelines | tools: read, grep, find, ls, bash, edit, write | model: (default)

## Extensions and tools
- `docs-context` — tools: refresh_docs_index
- `project-policy` — tools: (no registered tool found by simple scan)
- `recent-changes` — tools: audit_recent_changes, list_recent_changes
- `specialist-delegate` — tools: delegate_specialist

## External runtime packages
- `pi-web-access` — Web search, URL fetching, GitHub repo cloning, PDF extraction, YouTube video understanding, and local video analysis for Pi coding agent | tools: fetch_content, get_search_content, web_search | installed (0.10.3)
