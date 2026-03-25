# Main Sequence project flow

This is Astro's default workflow.

## Goal

Turn a user request into a real Main Sequence project, then hand implementation to a coding specialist.

## Workflow

1. Read Astro docs and relevant Main Sequence docs.
2. Translate the user request into:
   - a project brief
   - a task list
   - acceptance criteria
3. Verify `mainsequence` authentication.
4. Create or select the Main Sequence project.
5. Set it up locally.
6. Write the target project's `astro/` handoff files.
7. Delegate implementation to `mainsequence-project-coder`.
8. Review status directly or through `doc-bug-auditor`.

## Handoff files

Astro maintains these files inside the checked-out project's `astro/` folder:

- `astro/brief.md`
- `astro/tasks.md`
- `astro/record.md`
- `astro/status.md`

`astro/status.md` should include concrete failure evidence when blocked:

- exact command or action
- working directory or target path
- exit code if known
- traceback, stderr excerpt, or log snippet
- what was already tried
- next recovery step

## Secrets used by this workflow

Main Sequence login:

- `astro-mainsequence-email`
- `astro-mainsequence-password`

GitHub issue escalation when needed:

- `astro-github-token`
- `astro-github-user` as optional metadata

## Delegation rules

- use `mainsequence-project-coder` for implementation in the target project
- use `doc-bug-auditor` for structured review or upstream SDK investigation

When the target project has its own:

- `AGENTS.md`
- `.agents/skills/mainsequence-project/SKILL.md`

those files are canonical for implementation inside that project.

## Related pages

- [`../components/agents.md`](../components/agents.md)
- [`tutorial-verification.md`](./tutorial-verification.md)

