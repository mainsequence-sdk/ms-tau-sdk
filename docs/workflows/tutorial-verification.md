# Tutorial verification

This is Astro's fixed regression workflow for the official Main Sequence tutorials.

## Goal

Verify that the documented CLI and GUI tutorial flows still match the real product and SDK behavior.

This workflow requires `ADD_TUTORIAL_AGENT=1` to enable the `rpro-builder` specialist.

## Canonical sources

- CLI tutorial:
  - `https://github.com/mainsequence-sdk/mainsequence-sdk/tree/main/docs/tutorial`
- GUI tutorial:
  - `https://github.com/mainsequence-sdk/mainsequence-sdk/tree/main/docs/tutorial_gui`

## Workflow

1. Read the upstream tutorials.
2. Derive the SDK version from the upstream repository, preferably `pyproject.toml`.
3. Create a disposable project named `tutorial_review_[sdk_version]`.
4. Delegate the build step to `rpro-builder`.
5. Validate the CLI tutorial first.
6. Validate GUI-validatable steps second with Playwright.
7. Open GitHub issues only for tutorial-documentation problems.
8. Always attempt backend cleanup before finishing.

## Rules

- use the same `astro-mainsequence-email` and `astro-mainsequence-password` secrets for CLI and GUI sign-in
- only raise issues for stale or missing tutorial instructions
- do not convert unrelated product or infrastructure bugs into tutorial issues
- if a GUI-validatable step is missing from `docs/tutorial_gui`, record that as tutorial drift

## Cleanup result states

- `passed`
- `passed_with_cleanup_warning`
- `failed`
- `failed_with_cleanup_error`

## Issue escalation

This workflow uses:

- `astro-github-token`
- `astro-github-user` as optional metadata

and should use GitHub REST rather than `gh`.

## Related pages

- [`main-sequence-project-flow.md`](./main-sequence-project-flow.md)
- [`../components/prompts.md`](../components/prompts.md)
