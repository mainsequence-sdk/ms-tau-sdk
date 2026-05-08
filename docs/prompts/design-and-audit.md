# `design-and-audit`

Source: [`pi/prompts/design-and-audit.md`](../../pi/prompts/design-and-audit.md)

## Frontmatter

- `name: review-main-sequence-project`
- description: review a checked-out Main Sequence project and report whether work is finished, blocked, or failing

## Purpose

Provide a compact review workflow for a checked-out project.

## Workflow summary

1. inspect task, status, planning, or tracking files in the checked-out project
2. use old `astro/` files only as supporting evidence
3. summarize the current state with:
   - overall state
   - completed tasks
   - open tasks
   - blockers or failure causes
   - next actions

## Related files

- [`../../.pi/agents/mainsequence-project-executor.md`](../../.pi/agents/mainsequence-project-executor.md)
