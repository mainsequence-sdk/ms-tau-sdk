---
name: verify-mainsequence-tutorial
description: Verify Main Sequence CLI and GUI tutorials against a disposable tutorial review project.
---

Task: $ARGUMENTS

Follow this workflow:

1. Treat the upstream Main Sequence SDK tutorial sources as canonical for this run:
   - CLI tutorial: `https://github.com/mainsequence-sdk/mainsequence-sdk/tree/main/docs/tutorial`
   - GUI tutorial: `https://github.com/mainsequence-sdk/mainsequence-sdk/tree/main/docs/tutorial_gui`
2. Read the upstream SDK version from the Main Sequence SDK repository itself, preferably from `pyproject.toml`, and use that exact version in the disposable project name `tutorial_review_[sdk_version]`.
3. Create a disposable Main Sequence project named `tutorial_review_[sdk_version]`.
4. Prepare local review notes:
   - `tutorial-review/brief.md`
   - `tutorial-review/tasks.md`
   - `tutorial-review/record.md`
   - `tutorial-review/status.md`
5. Validate the CLI tutorial first.
   - Follow the official tutorial instructions.
   - Record exact commands, working directories, exit codes, and mismatches.
6. Validate GUI tutorial steps only when they are actually GUI-validatable in the current environment.
   - Record missing GUI instructions separately from product or infrastructure failures.
7. Open or recommend documentation issues only for tutorial documentation changes, missing instructions, or outdated guidance in `docs/tutorial` or `docs/tutorial_gui`.
8. Cleanup is mandatory.
   - Delete the disposable `tutorial_review_[sdk_version]` project from the backend when cleanup is available.
   - Record the exact cleanup command and result.
9. Return:
   - overall status
   - SDK version
   - project id or name
   - local checkout path
   - CLI validation result
   - GUI validation result
   - tutorial mismatches
   - documentation issue status
   - cleanup status
   - next actions
