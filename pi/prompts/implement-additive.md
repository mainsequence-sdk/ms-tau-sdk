---
name: orchestrate-main-sequence-project
description: Select or create a Main Sequence project, set it up locally, and launch the coding specialist.
---

Task: $ARGUMENTS

Follow this workflow:

1. Read relevant Main Sequence docs or CLI guidance, plus the target project's own context when available.
2. Translate the user request into:
   - a short brief
   - a concrete task list
   - acceptance criteria
   - a proposed project name only when the request may require creating a new project
3. Decide whether the user wants to work on an existing project or create a new one.
   - If the user wants to work on an existing project:
     - Treat that as an existing-project flow, not a creation flow.
     - Use the Main Sequence CLI to search for matching projects and ask the user to confirm the exact project when needed.
     - Once confirmed, the orchestrator owns the selected project id, selected project name, and local setup flow.
   - If the user wants to create a new project:
     - Load and follow the `mainsequence-project-creation` skill to collect the required project creation intake.
     - Do not validate the name or create the project until that skill has produced a concrete brief, task list, acceptance criteria, and a confirmed or user-provided project name.
     - Validate the name with `mainsequence project validate-name "<name>"`.
     - Create the project with `mainsequence project create "<name>"`.
4. Set up the selected or created project locally with `mainsequence project set-up-locally <id>`.
   - The orchestrator always owns this step.
   - Resolve and keep the checked-out local path before any delegation.
   - If the exact local checkout path is not known, stop instead of delegating.
5. If the workflow or target project uses project-local tracking or status files, keep them current in the checked-out project.
   - Do not create or require `astro/brief.md`, `astro/tasks.md`, `astro/record.md`, or `astro/status.md` as a default contract.
   - Prefer the checked-out project's own instructions, task files, status files, and planning files when they exist.
   - For failures or blockers recorded in project-local tracking, include the command or action attempted, the relevant path, the exit code when known, the error evidence, and the best next action.
6. Call `delegate_specialist` with `mainsequence-project-coder` and set `cwd` to the checked-out project folder.
   - Delegate only after the checked-out local path is known.
   - Pass the checked-out target project folder as `cwd`.
   - Pass the selected Main Sequence project id as `projectId`.
   - Never delegate using only a project id or an unresolved project reference.
   - Never delegate without both `cwd` and `projectId`.
   - The child should read any available project-local instructions and project-local skills when they exist.
   - Treat those project-local instructions and project-local skills as canonical for implementation and build conventions.
7. Return a concise summary of the project context, the local checkout path, the current state, and the next step.
