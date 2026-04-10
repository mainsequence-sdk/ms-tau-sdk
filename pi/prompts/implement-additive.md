---
name: orchestrate-main-sequence-project
description: Create or update a Main Sequence project, prepare project handoff files, and launch the coding subagent.
---

Task: $ARGUMENTS

Follow this workflow:

1. Read relevant Main Sequence docs or CLI guidance, plus the target project's own context when available.
2. Translate the user request into a project brief, task list, and acceptance criteria.
3. Verify authentication with `mainsequence user`.
   - This workflow requires `MAINSEQUENCE_ACCESS_TOKEN`, `MAINSEQUENCE_REFRESH_TOKEN`, `MAINSEQUENCE_BACKEND`, and `MAINSEQUENCE_PROJECTS_BASE` in the environment.
   - If auth is missing or expired, run:
     `mainsequence login --access-token "$MAINSEQUENCE_ACCESS_TOKEN" --refresh-token "$MAINSEQUENCE_REFRESH_TOKEN" --backend "$MAINSEQUENCE_BACKEND" --projects-base "$MAINSEQUENCE_PROJECTS_BASE"`.
   - If `MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS` is set, keep refreshing tokens on that interval while the session is active.
   - Do not request username/password credentials.
   - If authentication fails or any env vars are missing, stop and ask the user to refresh them.
   - If GitHub issue escalation is part of the task, retrieve GitHub credentials only from system secrets named `astro-github-token` and, optionally, `astro-github-user`.
   - `astro-github-token` must be the classic GitHub personal access token for this workflow, not a fine-grained PAT.
   - Do not look for or use any other GitHub credentials, and do not require a second user confirmation before opening an upstream issue once the escalation rules are met.
   - Use the retrieved PAT directly in the REST request or place it only in a short-lived local shell variable such as `ASTRO_GITHUB_TOKEN`.
4. Create the project with `mainsequence project create`.
5. Set it up locally with `mainsequence project set-up-locally`.
   - When running in containers, use the repo root `Dockerfile` which includes Python and Node in the same app container.
   - Run Python commands inside this same app container (do not use a separate Python-only container).
   - The container image installs `mainsequence` from `MAINSEQUENCE_PIP_SPEC` in the final Docker layer.
   - If you use that image, mount only the exact host directories needed:
     - `~/.pi/agent` to `/root/.pi/agent`
     - `~/mainsequence` to `/root/mainsequence`
     - `~/mainsequence-dev` to `/root/mainsequence-dev`
6. Update `astro/brief.md`, `astro/tasks.md`, `astro/record.md`, and `astro/status.md` in the checked-out project.
   - `astro/brief.md`: translated user intent, project goal, and acceptance criteria
   - `astro/tasks.md`: prioritized actionable tasks with checkboxes or statuses
   - `astro/record.md`: project id or name, local checkout path, and orchestration notes
   - `astro/status.md`: latest state, evidence checked, blockers or failures, and next actions
     - for failures or blockers, include the exact command or action attempted, the working directory or target path when relevant, the exit code if known, and a traceback, stderr excerpt, or log snippet
     - include concrete identifiers such as the failing file, script, job id, run id, or URL when available
     - include what was already tried and the best next action
7. Call `delegate_specialist` with `mainsequence-project-coder` and set `cwd` to the checked-out project folder.
   - The child should read the target project's `AGENTS.md` and `.agents/skills/mainsequence-project/SKILL.md` when they exist.
   - Treat those target-project files as canonical for implementation and build conventions.
8. Return:
   - project id or project name
   - local checkout path
   - current task status
   - blockers or next actions
