---
description: Verify the official Main Sequence CLI and GUI tutorials against a disposable tutorial review project.
---

Task: $ARGUMENTS

Follow this workflow:

1. Read `docs/workflows/tutorial-verification.md`.
2. Treat these upstream tutorial sources as canonical for this run:
   - CLI tutorial: `https://github.com/mainsequence-sdk/mainsequence-sdk/tree/main/docs/tutorial`
   - GUI tutorial: `https://github.com/mainsequence-sdk/mainsequence-sdk/tree/main/docs/tutorial_gui`
3. Read the upstream SDK version from the Main Sequence SDK repository itself, preferably from `pyproject.toml`, and use that exact version in the disposable project name `tutorial_review_[sdk_version]`.
4. Retrieve Main Sequence credentials from the same system secrets the parent orchestrator already uses:
   - `astro-mainsequence-email`
   - `astro-mainsequence-password`
   On macOS, prefer reading them through the `security` CLI instead of asking the user again.
5. Verify `mainsequence user`, then use `mainsequence login <email>` if needed.
6. Create the disposable Main Sequence project named `tutorial_review_[sdk_version]`, set it up locally, and prepare `astro/brief.md`, `astro/tasks.md`, `astro/record.md`, and `astro/status.md`.
   - Split the task plan into CLI validation, GUI validation, tutorial mismatch review, tutorial-only GitHub issue escalation, and backend cleanup.
7. Call `delegate_specialist` with `rpro-builder`, set `cwd` to the checked-out project folder, and pass a custom task that tells it to:
   - build the disposable tutorial review project named `tutorial_review_[sdk_version]`
   - follow the fixed `rpro-builder` guidelines
   - follow the official CLI tutorial path first
   - keep exact evidence in `astro/status.md`
   - treat the project as disposable and be ready to delete it from the backend through the Main Sequence CLI when the delegated task asks for cleanup
8. Validate the CLI tutorial yourself after the builder step.
   - Follow the official tutorial instructions through the CLI first.
   - Record exact commands, working directories, exit codes, and mismatches.
9. Validate the GUI tutorial second, but only for steps that are actually GUI-validatable.
   - Use Playwright for browser automation and evidence capture.
   - Use the same `astro-mainsequence-email` and `astro-mainsequence-password` secrets for GUI sign-in.
   - If GUI coverage for a GUI-validatable step does not exist in `docs/tutorial_gui`, create a concrete suggested tutorial update in `astro/status.md`.
   - If the SDK or web product changed, document the mismatch precisely in `astro/status.md`.
10. GitHub issue rules for this workflow:
   - open GitHub issues only for tutorial documentation changes, missing instructions, or outdated guidance in `docs/tutorial` or `docs/tutorial_gui`
   - do not open issues for unrelated product bugs, auth problems, infrastructure failures, or generic SDK defects unless the problem is specifically that the tutorial docs are wrong or missing
   - use only `astro-github-token` and optional `astro-github-user`
   - `astro-github-token` must be the classic GitHub personal access token Astro uses for this workflow, not a fine-grained PAT
   - prefer GitHub REST API over `gh`
   - do not require an extra user confirmation before opening a tutorial-only issue once the evidence and duplicate-check rules are satisfied
11. Cleanup is mandatory for this workflow.
   - Always attempt backend cleanup through the Main Sequence CLI before finishing.
   - Delete the disposable `tutorial_review_[sdk_version]` project from the backend and record the exact cleanup command and result.
   - If validation passed but cleanup failed, overall status must be `passed_with_cleanup_warning`.
   - If main validation failed and cleanup also failed, overall status must be `failed_with_cleanup_error`.
12. Return:
   - overall status
   - SDK version
   - project id or name
   - local checkout path
   - CLI validation result
   - GUI validation result
   - tutorial mismatches
   - tutorial-only GitHub issue status
   - cleanup status
   - next actions
