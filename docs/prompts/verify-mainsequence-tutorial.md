# `verify-mainsequence-tutorial`

Source: [`pi/prompts/verify-mainsequence-tutorial.md`](../../pi/prompts/verify-mainsequence-tutorial.md)

## Purpose

Run Astro's fixed tutorial-regression workflow against a disposable Main Sequence project.

## Workflow summary

1. treat the upstream CLI and GUI tutorial directories as canonical
2. derive the SDK version from the upstream repository
3. treat CLI auth as runtime-managed
4. call `ensure_mainsequence_cli_auth` once on auth failure, then retry
5. create and set up the disposable tutorial review project
6. delegate checked-out project work to `mainsequence-project-coder`
7. validate the CLI tutorial directly
8. validate GUI steps with Playwright when possible
9. open GitHub issues only for tutorial-documentation defects
10. perform backend cleanup before finishing

## Required return shape

- overall status
- SDK version
- project id or name
- local checkout path
- CLI result
- GUI result
- tutorial mismatches
- tutorial-only issue status
- cleanup status
- next actions

## Related files

- [`../extensions/tools/mainsequence-cli-auth.md`](../extensions/tools/mainsequence-cli-auth.md)
- [`../extensions/tools/specialist-delegate/README.md`](../extensions/tools/specialist-delegate/README.md)
