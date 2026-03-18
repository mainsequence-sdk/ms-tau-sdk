---
name: orchestrate-main-sequence-project
description: Create or update a Main Sequence project, prepare Astro handoff files, and launch the coding subagent.
---

Task: $ARGUMENTS

Follow this workflow:

1. Read Astro repo context and `docs/main-sequence-orchestration.md`.
2. Translate the user request into a project brief, task list, and acceptance criteria.
3. Verify authentication with `mainsequence user`, and if needed use `mainsequence login`.
   - When login needs credentials, retrieve them from system secrets named `astro-mainsequence-email` and `astro-mainsequence-password`.
   - On macOS, prefer reading them through the `security` CLI instead of asking the user again.
4. Create the project with `mainsequence project create`.
5. Set it up locally with `mainsequence project set-up-locally`.
   - When Python or `mainsequence` commands need an isolated runtime, prefer the repo root `Dockerfile` instead of the host system Python.
   - If you use that image, mount the whole host `~/mainsequence` root to `/Users/<user>/mainsequence` inside the container.
6. Update `astro/brief.md`, `astro/tasks.md`, `astro/record.md`, and `astro/status.md` in the checked-out project.
   - `astro/brief.md`: translated user intent, project goal, and acceptance criteria
   - `astro/tasks.md`: prioritized actionable tasks with checkboxes or statuses
   - `astro/record.md`: project id or name, local checkout path, and orchestration notes
   - `astro/status.md`: latest state, evidence checked, blockers or failures, and next actions
7. Call `delegate_specialist` with `mainsequence-project-coder` and set `cwd` to the checked-out project folder.
   - The child should read the target project's `AGENTS.md` and `.agents/skills/mainsequence-project/SKILL.md` when they exist.
   - Treat those target-project files as canonical for implementation and build conventions.
8. Return:
   - project id or project name
   - local checkout path
   - current task status
   - blockers or next actions
