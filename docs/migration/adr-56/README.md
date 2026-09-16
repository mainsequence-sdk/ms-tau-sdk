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
| SDK code | C3 — new package and public API | Complete | `ms-tau-sdk` 0.1.0 builds and clean-installs; `ms_tau_sdk` exports `create_app` and `TauSDKSettings`; `ms-tau` starts from a validated project workspace and serves health/version with no retired import namespace. |
| SDK code | C4 — project consumption | Complete | The locked fixture project depends on `ms-tau-sdk==0.1.0`, owns a three-line ASGI shim and `.tau`, loads its extension in its own environment, runs installed `ms-tau`, serves health, and shuts down cleanly. |
| SDK code | C5 — remove retired surface | Complete | Production source/tests use only the SDK identity; compatibility test names are gone; former decisions/docs are in a non-normative historical archive. |
| SDK code | C6 — release candidate | Complete | Allowlisted wheel/sdist contents, dependency metadata, checksums, provenance, CI attestation, protected PyPI automation, and a live clean-install process gate. |
| SDK code | C7 — stable SDK release | Complete | `1.0.0` metadata, public/compatibility/ownership docs, root and consumer locks, full tests/coverage, allowlisted artifacts, and isolated installed-process checks pass; tag/registry publication remains an explicit release-owner action. |
| Architecture decisions | A1–A4 — re-adopt, amend, eliminate, index | Complete | ADRs 0001–0004 are the reviewed SDK decision set; the former decision corpus is historical only. |
| Tests | T1–T5 — contracts through cutover | Complete | Black-box, SDK unit, project Tau configuration, distribution, and clean consumer coverage are active; migration-only test names are removed. |
| Documentation | D1–D5 — maintainer, author, reference, migration, cutover | Complete | New SDK documentation is normative; migration and former-project material are explicitly separated. |

All repository implementation tracks are complete. No external repository inventory or
modification was an ADR 56 gate.

Post-cutover cleanup removed the remaining archived provider-specific image-build/push procedure
references and added a repository-scope regression contract.

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
