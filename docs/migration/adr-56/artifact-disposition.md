# Initial SDK Source Artifact Disposition

Status: Phase C0 working ledger

Date: 2026-09-16

This ledger starts at subsystem/file-family granularity. Phase C0 expands candidate Python SDK
families to a file-level reviewed outcome before any source tree is copied or renamed. Container,
image-publication, and deployment artifacts are not candidates for migration into the SDK.

| Current artifact | Responsibility | Proposed disposition | Target |
| --- | --- | --- | --- |
| `src/astro/app.py` | FastAPI factory, lifespan, Uvicorn runner | Refactor and migrate | Public `ms_tau_sdk` application factory plus `ms-tau` runner; remove import-time operational coupling. |
| `src/astro/api/` | Health, chat, sessions, responses, A2A transports | Amend and migrate | Private/public router primitives selected during C1; preserve accepted wire contracts. |
| `src/astro/backend/auth.py` | Runtime credential exchange/cache | Migrate behind public SDK lifecycle | Retain `MAINSEQUENCE_RUNTIME_CREDENTIAL_*`; expose no secret-bearing public object. |
| `src/astro/backend/client.py`, `models.py`, `routes.py` | Backend protocol client and models | Amend and migrate privately | SDK backend adapter with contract tests and protocol compatibility metadata. |
| `src/astro/backend/mcp.py` | Main Sequence MCP connection/catalog | Amend and migrate | SDK integration primitive with startup and shutdown ownership. |
| `src/astro/providers/` | Provider-control validation and provider construction | Amend and migrate privately | SDK provider runtime primitive; preserve backend-authoritative evidence. |
| `src/astro/runtime/manager.py` | Durable session bootstrap, leases, registry, lifecycle | Major refactor and migrate | Explicit SDK runtime manager with injectable construction and workspace-required settings. |
| `src/astro/runtime/session.py` | Loaded durable Tau session and event flow | Migrate after boundary cleanup | SDK durable-session primitive. |
| `src/astro/runtime/events.py`, `observability.py`, `provenance.py`, `snapshots.py`, `task_context.py`, `failures.py` | Event translation, telemetry, provenance, snapshot and task helpers | Individually amend/migrate | Private SDK runtime modules unless a public embedding need is proven. |
| `src/astro/runtime/extensions.py` | Project-extension diagnostics | Replace around Tau-native configuration | Keep diagnostics; remove deployment enable-gate semantics. |
| `src/astro/sessions/storage.py` | Backend session storage and turn commits | Amend/reissue and migrate | SDK persistence primitive governed by reissued ADR 48. |
| `src/astro/protocols/` | A2A/assistant-ui/strict-JSON protocol helpers | Re-adopt or amend file by file | SDK protocol modules backed by black-box fixtures. |
| `src/astro/tools/mainsequence_mcp.py` | MCP tool adapters and resource prompt | Amend and migrate | SDK Main Sequence integration; prompt composition goes through Tau's one configuration model. |
| `src/astro/tools/task_control.py` | Required A2A task tools | Amend and migrate | SDK protocol-required tools. |
| `src/astro/resources/` | Packaged Astro prompt/default resources | Refactor before migration | Packaged SDK Tau defaults resolved through Tau-native precedence; no second SDK configuration format. |
| `src/astro/settings.py` | Astro environment/settings contract | Redesign | New SDK settings and CLI contract; retain stable Main Sequence credential names, eliminate Astro ontology. |
| `src/astro/logging.py`, `errors.py` | Logging and errors | Amend and migrate | New project vocabulary and stable SDK error surface. |
| `src/astro/agents/` | Sessionless execution snapshot model | Pending detailed review | Preserve sessionless behavior if re-adopted; remove role-specific assumptions. |
| `src/astro/__init__.py` | Astro version/package identity | Eliminate | New `ms_tau_sdk.__init__` with independent version line. |
| `pyproject.toml` | `mainsequence-astro`, `astro-stream`, build/test config | Replace | New project metadata, package layout, `ms-tau`, public exports, and quality gates. |
| `uv.lock` | SDK development lock | Regenerate | New SDK development lock; project consumers own their application locks. |
| `.env.example` | Astro environment contract | Rewrite | SDK/local-project variables with no obsolete Astro settings. |
| `.github/workflows/quality.yml` | Current source quality | Amend | New package, clean install, distribution inspection, docs, contract, and project-fixture gates. |
| `README.md`, `CHANGELOG.md` | Astro product identity/history | Replace active surface; preserve history | New SDK README/changelog plus explicit Astro migration/history links. |

## C0 Container and Deployment Deletion

The former Dockerfiles, Docker ignore files, Compose file, runtime wheelhouse lock, Kubernetes/GCP
deployment assets, image verifier, and deployment-only test were deleted at C0. They are not
assigned migration owners or replacement SDK artifacts. Platform repositories own any future
project-image build or deployment implementation.

## Later Cross-Repository Cutover Inventory

An initial read-only tdag-django inventory is recorded in
[`django-inventory.md`](./django-inventory.md). The following require implementation in that owning
repository or additional external inventory before the platform cutover phases, not before SDK C1:

- project templates and existing TAU-enabled consumer repositories;
- package-index publication and credential ownership; and
- dashboards, alerts, and inventory queries keyed by Astro release or legacy roles.
