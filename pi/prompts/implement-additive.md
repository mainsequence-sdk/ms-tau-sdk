---
name: orchestrate-main-sequence-project
description: Select or create a Main Sequence project, set it up locally, and launch the coding specialist.
---

Task: $ARGUMENTS

Follow this workflow:

1. Read relevant Main Sequence docs or CLI guidance, plus the target project's own context when available.
2. Decide whether the user wants to work on an existing project or create a new one.
   - If the user wants to work on an existing project:
     - Treat that as an existing-project flow, not a creation flow.
     - Do not require a new brief, task list, or acceptance criteria just to open or resume the project.
     - Use the Main Sequence CLI to search for matching projects and ask the user to confirm the exact project when needed.
     - Once confirmed, the orchestrator owns the selected project id, selected project name, and local setup flow.
     - If the request already contains a concrete implementation task, capture it clearly and pass it as `initialTask` when switching into the project session.
     - If the request does not yet contain a concrete implementation task, switch into a project-scoped coding session without asking the user to restate a first task.
   - If the user wants to create a new project:
     - Load and follow the `mainsequence-project-creation` skill to collect the required project creation intake.
     - Do not validate the name or create the project until that skill has produced a concrete brief, task list, acceptance criteria, and a confirmed or user-provided project name.
     - Before creating the project, resolve the GitHub organization with `mainsequence organization github-organizations --json`.
       - If exactly one GitHub organization is returned, use its id without asking for confirmation.
       - If more than one GitHub organization is returned, ask the user to choose which organization should own the new project.
       - If no GitHub organization is returned, stop and report that project creation cannot continue because no GitHub organization is available.
       - If the command is unavailable, exits nonzero, or returns `No such command`, stop and report that
         failure using the global Main Sequence CLI failure contract. Do not call
         `mainsequence project create`.
       - Keep the selected GitHub organization id as `githubOrgId` for the create command.
     - Validate the name with `mainsequence project validate-name "<name>"`.
     - Create the project with `mainsequence project create "<name>" --github-org-id <githubOrgId>`.
     - Do not call `mainsequence project create "<name>"` without `--github-org-id`.
     - Do not retry project creation with guessed flags or alternate interactive paths after a
       non-auth failure.
     - Report non-auth CLI failures using the global Main Sequence CLI failure contract.
3. Set up the selected or created project locally with `tsx /app/scripts/mainsequence_project_set_up_locally.ts <id>`.
   - The orchestrator always owns this step.
   - Do not call raw `mainsequence project set-up-locally <id>` directly when running inside Astro.
   - After project creation, do not hand off, do not copy `project_blueprint.md`, and do not treat the checkout as ready until the project details report `is_initialized=true`.
   - Query the project's details with the Main Sequence CLI and check the `is_initialized` field before continuing.
   - If `is_initialized` is still false, wait/retry that project-details check instead of switching sessions or copying files early.
   - Resolve and keep the checked-out local path before any delegation.
   - If the exact local checkout path is not known, stop instead of delegating.
4. If the workflow or target project uses project-local tracking or status files, keep them current in the checked-out project.
   - Do not create or require `astro/brief.md`, `astro/tasks.md`, `astro/record.md`, or `astro/status.md` as a default contract.
   - Prefer the checked-out project's own instructions, task files, status files, and planning files when they exist.
   - For failures or blockers recorded in project-local tracking, include the command or action attempted, the relevant path, the exit code when known, the error evidence, and the best next action.
5. Use `switch_project_session` when the active conversation should move into the checked-out project's `mainsequence-project-coder` session.
   - Call it only after the checked-out local path is known and the project reports `is_initialized=true`.
   - Pass the checked-out target project folder as `cwd`.
   - Pass the selected Main Sequence project id as `projectId`.
   - Use `initialTask` only when the current request already contains concrete project-local work.
   - Do not claim a project-session switch in plain text without calling this tool.
   - After calling `switch_project_session`, stop instead of continuing project-local work in the orchestrator session.
6. Use `delegate_specialist` only for bounded background specialist work that should not replace the active project session.
   - If you delegate `mainsequence-project-coder` for a short-lived subtask, still pass both `cwd` and `projectId`.
   - The child should read any available project-local instructions and project-local skills when they exist.
   - Treat those project-local instructions and project-local skills as canonical for implementation and build conventions.
7. Return a concise summary of the project context, the local checkout path, the current state, and the next step.
