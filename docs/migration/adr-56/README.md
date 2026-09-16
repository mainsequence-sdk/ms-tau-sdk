# ADR 56 Migration Workspace

This directory is the execution ledger for
[ADR 56](../../adrs/adr-56-main-sequence-tau-sdk-workspace-bound-library-deployment.md).
ADR 56 creates Main Sequence TAU SDK as a new project and retires Astro as a deployment product.

The documents here are migration evidence, not new architecture decisions. Architecture changes
that are not already decided by ADR 56 require a new or amended ADR.

## Current Stage

Started: 2026-09-16

Source baseline: Astro Tau `v4.0.25`, commit `9b7a1c5`, branch `development`

| Track | Phase | Status | Exit evidence |
| --- | --- | --- | --- |
| SDK code | C0 — freeze reusable behavior | Complete | Green baseline, HTTP operation contract, approved charter, complete SDK artifact disposition, and no container/deployment assets. |
| Architecture decisions | A0 — inventory and dependency map | Complete | Active and referenced repository-local decisions have an owner and disposition. |
| Tests | T0 — inventory and classify | Complete | Every tracked test module and fixture family is classified; image-only tests are deleted. |
| Documentation | D0 — inventory and terminology map | Complete | Every tracked documentation family has an owner and destination. |
| SDK code | C1 — internal library boundaries | Complete | Explicit application service graph owns startup/shutdown; CLI constructs an app instance; sessionless harness construction is isolated; all routes and contracts remain green. |
| SDK code | C2 — Tau configuration | Complete | Tau 0.4.2 native project prompt precedence, skills, prompt templates, hooks, extensions, diagnostics, reload, and shutdown are exercised; no SDK extension gate or parallel prompt configuration remains. |
| SDK code | C3 — new package and public API | In progress | Rename distribution/import/command, require the current project workspace, and remove Astro terminology from production surfaces. |

C1 may start when the local SDK-behavior, decision, test, and documentation inputs needed for that
boundary are classified. No external repository inventory or modification is an ADR 56 gate.

## Ledgers

- [Project charter](./project-charter.md)
- [Baseline evidence](./baseline.md)
- [ADR disposition](./adr-disposition.md)
- [SDK source artifact disposition](./artifact-disposition.md)
- [Test disposition](./test-disposition.md)
- [Documentation disposition](./documentation-disposition.md)

## Disposition Vocabulary

Every migration input uses one of these outcomes:

- **Re-adopt** — accept the behavior explicitly into the new project.
- **Amend/reissue** — keep the intent but rewrite it for the SDK ontology.
- **Supersede/replace** — implement a new decision or primitive instead.
- **Eliminate** — remove it from the active project because it exists for the retired ontology.
- **Migration-only** — retain temporarily to prove or perform cutover, then delete.
- **Pending** — inventory is complete but the owner has not approved an outcome.

`Pending` is never permission to copy an artifact into the new project.

## Update Discipline

For each completed phase:

1. update the relevant disposition rows;
2. link code, test, documentation, or package evidence;
3. record verification commands and results in `baseline.md` or a phase-specific evidence file;
4. update ADR 56 implementation status; and
5. do not mark the phase complete while an exit-gate item remains pending.
