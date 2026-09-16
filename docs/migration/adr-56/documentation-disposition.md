# Documentation Disposition Matrix

Status: Phases D0 through D5 complete

Date: 2026-09-16

Owner for every active rewrite, archive, or deletion: Main Sequence TAU SDK. Directory-family rows
are exhaustive for the tracked documentation tree and are used intentionally where every file in a
historical family has the same destination.

| Current documentation family | Proposed disposition | Target action |
| --- | --- | --- |
| Root `README.md` | Replace active product document | New-project README covering install, `ms-tau`, workspace requirement, `.tau`, local run, and links. Preserve Astro history only in migration notes. |
| `CHANGELOG.md` | Close Astro series and start new history | Keep Astro 4.x entries as historical; new SDK uses independent versions and release notes. |
| `docs/README.md` | Replace navigation | New SDK docs information architecture and active/historical separation. |
| `docs/adrs/README.md` | Transition, then replace | ADR 56 remains the Astro transition decision; new project starts ADR 0001 and imports only reviewed decisions. |
| `docs/adrs/adr-47` through `adr-55` | Individual ADR disposition | Re-adopt/amend/supersede/eliminate according to `adr-disposition.md`; never copy all as accepted. |
| `docs/getting-started/` | Rewrite | Project installation, dependency lock, credentials, repository cwd, `ms-tau`, first session, and local troubleshooting. |
| `docs/interface/` | Amend/reissue | Preserve accepted wire/runtime contracts while replacing package, settings, role, and image assumptions. Generate examples against actual SDK OpenAPI. |
| `docs/a2a/` | Amend/reissue | Preserve accepted A2A contracts and clearly separate durable streaming from buffered sessionless responses. |
| `docs/reference/decisions.md` | Replace active index | New project ADR index is normative; Astro reference index becomes historical. |
| `docs/reference/adr-*` | Historical by default, individual audit where referenced | Retain provenance but remove active authority unless reissued. |
| `docs/reference/folder-structure.md` | Rewrite | Describe `src/ms_tau_sdk`, tests, packaged defaults, project fixtures, and no production Docker assets. |
| `docs/reference/scope.md` | Rewrite as project charter/scope | Align with the new ownership boundaries. |
| `docs/reference/persistent-state.md` | Amend/reissue | Preserve accepted state contracts through SDK persistence primitives. |
| `docs/reference/a2a-standard-client-examples.md` | Revalidate and amend | Run examples against the new service and remove Astro deployment assumptions. |
| `docs/implemenation_task/` | Archive or replace | Existing tasks are Astro migration history. New SDK implementation work is tracked under this ADR 56 migration workspace or new-project tasks. |
| `docs/bugs/` and `docs/investigations/` | Historical with explicit relevance review | Reopen only unresolved product behavior; do not treat old topology as normative. |
| Former `docs/reserach_guide/` | Historical | Archived with the former project; it is not SDK product documentation. |
| Former GCP, Kubernetes, Docker, and Compose material | Eliminate from SDK docs | The SDK documents installation and process startup only; project artifact construction and external deployment are out of scope. |
| `.env.example` | Rewrite and verify | New SDK variables and stable Main Sequence credential names; remove obsolete Astro settings. |

## New Documentation Required

- Main Sequence TAU SDK project charter.
- Install and lock the SDK in a project.
- Run `ms-tau` from a project environment.
- Public Python API and embedding guide.
- Single Tau `.tau` configuration and precedence guide.
- Project extension responsibility and shared trust-boundary guide.
- Durable `CodingSession` versus sessionless `AgentHarness` execution guide.
- External integration contract limited to what a host must provide to the SDK process.
- Runtime composition and troubleshooting guide.
- Astro-to-SDK migration guide with package/import/command/settings/resource mappings.
- Version compatibility and support policy.
- New active ADR index and historical Astro archive index.

Internal links are validated whenever a family moves. No active document is mechanically renamed
without checking whether its underlying concept still exists.

## Cutover Outcome

- `docs/README.md` and `docs/adrs/README.md` are the normative documentation and decision roots.
- Project-author, maintainer, configuration, runtime, settings, testing, compatibility, scope, and
  troubleshooting documents describe the implemented SDK.
- The explicit migration guide records removed and renamed surfaces.
- Former decisions, plans, references, investigations, bugs, and changelog are under
  `docs/history/astro` with a non-normative archive notice.
