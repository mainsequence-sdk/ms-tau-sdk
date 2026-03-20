---
name: review-main-sequence-project
description: Review a checked-out Main Sequence project and report whether work is finished, blocked, or failing.
---

Task: $ARGUMENTS

Follow this workflow:

1. Inspect the project's `astro/` files, especially `astro/tasks.md` and `astro/status.md`.
2. Call `delegate_specialist` with `doc-bug-auditor` and set `cwd` to the project folder.
3. Return the status review with:
   - overall state
   - completed tasks
   - open tasks
   - blockers or failure causes
   - upstream `mainsequence-sdk` assessment
   - GitHub issue status
   - next actions
