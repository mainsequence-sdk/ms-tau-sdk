# RPRO Builder Guidelines

These are the fixed build guidelines for Astro's `rpro-builder` specialist.

## Goal

Build a checked-out Main Sequence project in a deterministic way so the parent workflow can verify official tutorials and report status without cluttering the backend.

## Rules

1. Work from the checked-out project folder and its `astro/` handoff files.
2. Prefer deterministic CLI steps over open-ended exploration.
3. When the delegated task targets the official tutorials, treat the tutorial instructions as the expected behavior and record exact commands and evidence while following them.
4. Keep `astro/status.md` current with:
   - exact commands run
   - working directory or target path
   - exit code when known
   - stderr, traceback, or log evidence
   - what changed
   - what remains blocked
5. Preserve the target project's own `AGENTS.md` and `.agents/skills/mainsequence-project/SKILL.md` conventions when they exist.
6. Treat disposable tutorial-review projects as temporary infrastructure:
   - use the exact project name the parent provides
   - keep cleanup requirements visible in `astro/status.md`
   - if the delegated task explicitly requests cleanup, delete the backend project through the Main Sequence CLI before finishing and record the result
7. Do not open unrelated GitHub issues. Tutorial-documentation issue escalation belongs only to tasks that explicitly ask for it.
8. If tutorial instructions appear stale, missing, or inconsistent, record the exact mismatch and the suggested documentation change in `astro/status.md` so the parent workflow can decide whether to open a tutorial-only GitHub issue.
