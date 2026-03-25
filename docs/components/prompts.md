# Prompts

Prompts in `prompts/` are reusable workflow templates.

They are for repeatable parent-agent behavior that does not need a new extension.

## Current prompt templates

### `implement-additive.md`

Purpose:

- orchestrate a normal Main Sequence project request

This is the parent workflow for:

- project creation
- local checkout
- `astro/` handoff files
- delegation to `mainsequence-project-coder`

### `design-and-audit.md`

Purpose:

- review a checked-out Main Sequence project

This is the structured review path for status, blockers, and completion.

### `verify-mainsequence-tutorial.md`

Purpose:

- run the fixed tutorial-regression workflow

This is the source of truth for:

- reading the upstream CLI and GUI tutorials
- deriving the SDK version
- creating `tutorial_review_[sdk_version]`
- delegating the build to `rpro-builder`
- validating GUI steps with Playwright
- opening tutorial-only issues
- mandatory cleanup

## Why prompts exist separately from specialists

Prompts and specialists solve different problems:

- prompts guide the parent through a reusable workflow
- specialists define a narrower delegated role

## Related pages

- [`agents.md`](./agents.md)
- [`../workflows/tutorial-verification.md`](../workflows/tutorial-verification.md)

