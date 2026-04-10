---
description: Verify the official Main Sequence CLI and GUI tutorials against a disposable tutorial review project.
---

Task: $ARGUMENTS

Follow this workflow:

1. Treat these upstream tutorial sources as canonical for this run:
   - CLI tutorial: `https://github.com/mainsequence-sdk/mainsequence-sdk/tree/main/docs/tutorial`
   - GUI tutorial: `https://github.com/mainsequence-sdk/mainsequence-sdk/tree/main/docs/tutorial_gui`
2. Read the upstream SDK version from the Main Sequence SDK repository itself, preferably from `pyproject.toml`, and use that exact version in the disposable project name `tutorial_review_[sdk_version]`.
3. Ensure `MAINSEQUENCE_ACCESS_TOKEN`, `MAINSEQUENCE_REFRESH_TOKEN`, `MAINSEQUENCE_BACKEND`, and `MAINSEQUENCE_PROJECTS_BASE` are present in the environment.
   - Do not request or use username/password credentials.
   - If any env vars are missing, stop and ask the user to refresh them.
4. Verify `mainsequence user`.
   - If auth is missing or expired, run:
     `mainsequence login --access-token "$MAINSEQUENCE_ACCESS_TOKEN" --refresh-token "$MAINSEQUENCE_REFRESH_TOKEN" --backend "$MAINSEQUENCE_BACKEND" --projects-base "$MAINSEQUENCE_PROJECTS_BASE"`.
   - If `MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS` is set, keep refreshing tokens on that interval while the session is active.
   - If authentication fails, stop and record the failure in `astro/status.md`.
5. Create the disposable Main Sequence project named `tutorial_review_[sdk_version]`, set it up locally, and prepare `astro/brief.md`, `astro/tasks.md`, `astro/record.md`, and `astro/status.md`.
   - Split the task plan into CLI validation, GUI validation, tutorial mismatch review, tutorial-only GitHub issue escalation, and backend cleanup.
6. Call `delegate_specialist` with `mainsequence-project-coder`, set `cwd` to the checked-out project folder, and pass a custom task that tells it to:
   - work only inside the checked-out disposable tutorial review project
   - follow the official CLI tutorial path first
   - prefer deterministic CLI steps over open-ended exploration
   - record exact commands, working directories, exit codes, stderr excerpts, and mismatches in `astro/status.md`
   - treat the project as disposable and keep cleanup expectations visible for the parent workflow
7. Validate the CLI tutorial yourself after the coder step.
   - Follow the official tutorial instructions through the CLI first.
   - Record exact commands, working directories, exit codes, and mismatches.
8. Validate the GUI tutorial second, but only for steps that are actually GUI-validatable.
   - Use Playwright for browser automation and evidence capture.
   - Use an authenticated session derived from the environment tokens; do not prompt for username/password.
   - If GUI validation cannot proceed without interactive login, record the missing auth path in `astro/status.md` and mark the step as blocked.
   - If GUI coverage for a GUI-validatable step does not exist in `docs/tutorial_gui`, create a concrete suggested tutorial update in `astro/status.md`.
   - If the SDK or web product changed, document the mismatch precisely in `astro/status.md`.
9. GitHub issue rules for this workflow:
   - open GitHub issues only for tutorial documentation changes, missing instructions, or outdated guidance in `docs/tutorial` or `docs/tutorial_gui`
   - do not open issues for unrelated product bugs, auth problems, infrastructure failures, or generic SDK defects unless the problem is specifically that the tutorial docs are wrong or missing
   - use only `astro-github-token` and optional `astro-github-user`
   - `astro-github-token` must be the classic GitHub personal access token for this workflow, not a fine-grained PAT
   - prefer GitHub REST API over `gh`
   - do not require an extra user confirmation before opening a tutorial-only issue once the evidence and duplicate-check rules are satisfied
10. Cleanup is mandatory for this workflow.
   - Always attempt backend cleanup through the Main Sequence CLI before finishing.
   - Delete the disposable `tutorial_review_[sdk_version]` project from the backend and record the exact cleanup command and result.
   - If validation passed but cleanup failed, overall status must be `passed_with_cleanup_warning`.
   - If main validation failed and cleanup also failed, overall status must be `failed_with_cleanup_error`.
11. Return:
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
