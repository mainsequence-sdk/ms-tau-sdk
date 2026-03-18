# Architecture

## Goal

This repository is a Pi package that gives you a **Main Sequence project orchestration architecture** without modifying Pi core.

The design is intentionally split into layers:

1. **Static parent prompt** in `.pi/APPEND_SYSTEM.md`
2. **Pi runtime hooks and tools** in `extensions/`
3. **Specialist prompts** in `.pi/agents/`
4. **Generated documentation context** in `knowledge/`
5. **External Pi packages** referenced from `.pi/settings.json`
6. **Teaching material** in `tutorial/`

## Main pieces

### 1. Static parent prompt

Purpose:
- give the default Astro session its always-on orchestrator role

Why:
- the parent agent is the default Pi session
- the parent policy is mostly stable text now
- putting it in `.pi/APPEND_SYSTEM.md` makes the normal parent flow easier to inspect

### 2. `docs-context` extension

Purpose:
- append generated repository context at `before_agent_start`
- expose `refresh_docs_index` as an agent tool

Why:
- keeps the repo map always available
- avoids hard-coding lots of repo instructions into every specialist file
- avoids persistent duplicate messages by modifying the system prompt for the current turn only
- keeps docs refresh in the same TypeScript runtime as the rest of Astro

### 3. `project-policy` extension

Purpose:
- append a policy only for child specialist processes

Why:
- the parent prompt is already static in `.pi/APPEND_SYSTEM.md`
- child specialists should not recursively delegate again

### 4. `specialist-delegate` extension

Purpose:
- expose `delegate_specialist`
- discover `.pi/agents/*.md`
- spawn a child `pi` process
- stream child output back into the parent session

Why:
- this gives you a real subagent pattern without touching Pi core
- each specialist gets an isolated context window

### 5. `recent-changes` extension

Purpose:
- track files changed by the parent agent session
- expose `list_recent_changes`
- expose `audit_recent_changes`

Why:
- Astro-side edits are visible and auditable
- the audit step can still be a real tool when you change the orchestrator itself

### 6. External `pi-web-access` package

Purpose:
- expose standard Pi web and browsing tools
- avoid maintaining a redundant repo-local web wrapper

Why:
- external info changes faster than local repo rules
- Astro should reuse the community-standard package instead of duplicating it locally
- the package already covers search plus page and URL fetching through `web_search`, `fetch_content`, and `get_search_content`

## Why the parent agent orchestrates and the child implements

Astro makes `mainsequence-project-coder` an **implementation specialist** by default.

That is intentional.

The parent agent needs to stay focused on:

- understanding the user request
- driving the Main Sequence CLI
- preparing the project's `astro/` handoff files
- deciding when to review project status

The child specialist can then focus on implementation inside the checked-out Main Sequence project folder.

When the target project provides its own `AGENTS.md` or `.agents/skills/mainsequence-project/SKILL.md`, those files define the canonical implementation conventions for that project.

So the default is:

- parent orchestrates
- child implements in the target project folder
- parent reviews status directly or through `doc-bug-auditor`

## Runtime summary

- startup loads the local package through `.pi/settings.json`
- startup also loads `npm:pi-web-access` through `.pi/settings.json`
- parent sessions load `.pi/APPEND_SYSTEM.md` as the static orchestrator prompt
- before each prompt:
  - docs context is appended
  - child-specialist policy is appended only for child processes
- `refresh_docs_index` and `npm run docs:index` both call the same shared TypeScript generator
- external web tools are available from `pi-web-access`
- parent may call `delegate_specialist` with a target `cwd`
- child `pi` process runs a specialist in the checked-out project folder
- parent updates `astro/` handoff files and project metadata
- parent may call `doc-bug-auditor` for project status review
- parent may call `audit_recent_changes` when Astro itself changes

The repo also includes a small root `Dockerfile` for an isolated Python 3.11 runtime with `mainsequence` installed via `uv`.
