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
