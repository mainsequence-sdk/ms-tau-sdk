# Prompts

Prompts in `pi/prompts/` are reusable workflow templates.

They are for repeatable parent-agent behavior that does not need a new extension.

## Current prompt templates

### `pi/prompts/implement-additive.md`

Purpose:

- orchestrate a normal Main Sequence project request

This is the parent workflow for:

- existing-project selection
- project creation
- loading `mainsequence-project-creation` when a new project needs detailed intake
- local checkout
- project-local task and status tracking when the target project uses it
- delegation to `mainsequence-project-coder`

### `pi/prompts/design-and-audit.md`

Purpose:

- review a checked-out Main Sequence project

This is the structured review path for status, blockers, and completion.

### `pi/prompts/verify-mainsequence-tutorial.md`

Purpose:

- run the fixed tutorial-regression workflow as an explicit standalone path

This is the source of truth for:

- reading the upstream CLI and GUI tutorials
- deriving the SDK version
- creating `tutorial_review_[sdk_version]`
- delegating checked-out project work to `mainsequence-project-coder`
- validating GUI steps with Playwright
- opening tutorial-only issues
- mandatory cleanup

## Why prompts exist separately from specialists

Prompts and specialists solve different problems:

- prompts guide the parent through a reusable workflow
- specialists define a narrower delegated role

## Related pages

- [`agents.md`](./agents.md)
- [`settings-and-system-prompt.md`](./settings-and-system-prompt.md)
