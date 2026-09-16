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
| SDK code | C0 — freeze reusable behavior | In progress | Behavioral baseline captured and repository-owned container/deployment assets deleted; Python artifact ownership and streaming fixtures remain. |
| Architecture decisions | A0 — inventory and dependency map | In progress | Initial ADR 39/40/47–55 matrix created; older referenced decisions remain to classify. |
| Tests | T0 — inventory and classify | In progress | Current suite and initial file-level dispositions recorded; HTTP operation baseline is executable. |
| Documentation | D0 — inventory and terminology map | In progress | Initial document-family dispositions recorded; file-level expansion remains. |

C1 may start when the local SDK-behavior, decision, test, and documentation inputs needed for that
boundary are classified. Production consumer and platform inventories gate later cutover/deletion
phases; they do not block internal Python extraction.

## Ledgers

- [Project charter](./project-charter.md)
- [Baseline evidence](./baseline.md)
- [ADR disposition](./adr-disposition.md)
- [Source and deployment artifact disposition](./artifact-disposition.md)
- [Test disposition](./test-disposition.md)
- [Documentation disposition](./documentation-disposition.md)
- [tdag-django cross-repository inventory](./django-inventory.md)

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
2. link code, test, documentation, or deployment evidence;
3. record verification commands and results in `baseline.md` or a phase-specific evidence file;
4. update ADR 56 implementation status; and
5. do not mark the phase complete while an exit-gate item remains pending.
