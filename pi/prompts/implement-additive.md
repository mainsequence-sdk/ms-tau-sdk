---
name: orchestrate-main-sequence-project
description: Create or update a Main Sequence project, prepare project handoff files, and launch the coding subagent.
---

Task: $ARGUMENTS

Follow this workflow:

1. Read relevant Main Sequence docs or CLI guidance, plus the target project's own context when available.
2. Translate the user request into a project brief, task list, and acceptance criteria.
3. Decide whether the user wants to work on an existing project or create a new one.
   - If the user wants to work on an existing project:
     - Treat that as an existing-project flow, not a creation flow.
     - Use the Main Sequence CLI to search for matching projects and ask the user to confirm the exact project when needed.
     - Once confirmed, the orchestrator owns the selected project id, selected project name, and local setup flow.
   - If the user wants to create a new project:
     - Before `mainsequence project validate-name` or `mainsequence project create`, make sure the minimum project intake is known.
     - The minimum intake is:
       - the project goal
       - the key requirements or initial task list
       - the acceptance criteria
       - the project name, either provided by the user or explicitly confirmed after you propose one
     - If any required intake is missing, ask only for the missing pieces before continuing.
     - Propose a sensible project name when the user has not provided one.
     - Validate the name with `mainsequence project validate-name "<name>"`.
     - Create the project with `mainsequence project create "<name>"`.
4. Set up the selected or created project locally with `mainsequence project set-up-locally <id>`.
   - The orchestrator always owns this step.
   - Resolve and keep the checked-out local path before any delegation.
   - If the exact local checkout path is not known, stop instead of delegating.
5. Update `astro/brief.md`, `astro/tasks.md`, `astro/record.md`, and `astro/status.md` in the checked-out project.
   - `astro/brief.md`: translated user intent, project goal, and acceptance criteria
   - `astro/tasks.md`: prioritized actionable tasks with checkboxes or statuses
   - `astro/record.md`: project id or name, local checkout path, and orchestration notes
   - `astro/status.md`: latest state, evidence checked, blockers or failures, and next actions
     - for failures or blockers, include the exact command or action attempted, the working directory or target path when relevant, the exit code if known, and a traceback, stderr excerpt, or log snippet
     - include concrete identifiers such as the failing file, script, job id, run id, or URL when available
     - include what was already tried and the best next action
6. Call `delegate_specialist` with `mainsequence-project-coder` and set `cwd` to the checked-out project folder.
   - Delegate only after the checked-out local path is known.
   - Pass the checked-out target project folder as `cwd`.
   - Never delegate using only a project id or an unresolved project reference.
   - The child should read the target project's `AGENTS.md` and `.agents/skills/mainsequence-project/SKILL.md` when they exist.
   - Treat those target-project files as canonical for implementation and build conventions.
7. Return:
   - project id or project name
   - local checkout path
   - current task status
   - blockers or next actions
