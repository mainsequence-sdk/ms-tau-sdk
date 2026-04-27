# `implement-additive`

Source: [`pi/prompts/implement-additive.md`](../../pi/prompts/implement-additive.md)

## Frontmatter

- `name: orchestrate-main-sequence-project`
- description: select or create a Main Sequence project, set it up locally, and launch the coding specialist

## Purpose

This is the main parent workflow for normal Main Sequence project work.

## Workflow summary

1. inspect the user's request and relevant Main Sequence guidance
2. decide between existing-project and new-project flow
3. for new projects, use the project-creation skill before validating or creating
4. run `tsx /app/scripts/mainsequence_project_set_up_locally.ts <id>`
5. query the project details with the CLI and wait until `is_initialized=true`
6. preserve the checked-out path and project id
7. update any project-local tracking when it exists
8. use `switch_project_session` to hand work into `mainsequence-project-coder`
9. use `delegate_specialist` only for bounded background work

## Important constraints

- the orchestrator owns project selection and local setup
- inside Astro, do not call raw `mainsequence project set-up-locally <id>` directly
- new project creation must resolve a GitHub organization first and call
  `mainsequence project create "<name>" --github-org-id <githubOrgId>`
- if GitHub organization discovery or any other non-auth CLI step fails, report it using the global
  Main Sequence CLI failure contract instead of retrying guessed commands
- do not hand off or copy `project_blueprint.md` before the project details show `is_initialized=true`
- do not delegate coding work without both `cwd` and `projectId`
- do not claim a session switch without calling `switch_project_session`

## Related files

- [`../extensions/tools/switch-project-session.md`](../extensions/tools/switch-project-session.md)
- [`../extensions/tools/specialist-delegate/README.md`](../extensions/tools/specialist-delegate/README.md)
