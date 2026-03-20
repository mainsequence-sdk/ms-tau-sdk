# Specialists and routing

## What a specialist is here

A specialist is a markdown file in `.pi/agents/` with frontmatter and a prompt body.

In this starter:

- `mainsequence-project-coder`
- `doc-bug-auditor`

## What the delegate runtime does

`delegate_specialist`:

1. discovers specialists
2. reads frontmatter
3. spawns a child `pi` process
4. passes the specialist prompt through `--append-system-prompt`
5. streams child output back

That stream is not just the final answer. Astro forwards live child progress such as:

- partial assistant text
- tool calls as they are being prepared
- tool execution updates like the current `bash` command and recent output
- a short rolling trail of recent steps so you can still see what the specialist just checked

## Why the parent orchestrates instead of coding directly

The parent session is the source of truth for:

- user intent translation
- Main Sequence CLI actions
- `astro/` handoff files
- the final project summary

The child specialist can then focus on coding or review in the checked-out project folder.

## When to route where

### Use `mainsequence-project-coder`
When the task is:
- implementation-oriented
- scoped to the checked-out Main Sequence project
- ready to be executed from `astro/tasks.md`

Before it starts building, it should also read:
- the target project's `AGENTS.md`
- the target project's `.agents/skills/mainsequence-project/SKILL.md`

Those files are canonical for project-specific build and implementation conventions when they exist.

### Use `doc-bug-auditor`
When:
- you need to know whether project work is finished
- there are blockers or failures to explain
- you want a structured status review based on project evidence
- you want to know whether a failure is likely inside `mainsequence-sdk`
- you want duplicate-issue search and REST-based issue escalation for a likely upstream SDK bug

The auditor should keep the parent updated while it works by briefly announcing major steps such as reading `astro/status.md`, tracing a failing command, inspecting the local SDK install, checking the public upstream repo, or searching for duplicate issues.

When it needs to open an upstream GitHub issue, it should use only `astro-github-token` and optional `astro-github-user`, and it does not need a second user confirmation once the duplicate-check and evidence rules are satisfied.
