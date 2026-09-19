# Changelog

## 2.0.0 — 2026-09-19

- Retired Agent-targeted one-shot responses, the deployment execution snapshot setting, and
  Agent-UID-only provider hydration. Chat, local and durable A2A, and session snapshots remain.
- Aligned managed AgentTask execution with Django's canonical ADR-034 actions for dispatch claim,
  attempt start and settlement, and output create, append, and finalize. Removed the nonexistent
  attempt-Message and caller-delivery backend routes; signed caller delivery now persists its
  idempotent platform event before the runtime acknowledges Django's push.

## 1.2.3 — 2026-09-18

- Hard-cut the managed runtime contract to the canonical Tau 0.4.2 handshake: lease acquire and
  renew requests no longer send the retired `lease_purpose` field, and `tau_runtime_version`
  identifies the installed `tau-ai` distribution instead of the SDK package version.
- Added the Tau 0.4.2 `custom_message` discriminator to typed durable-entry responses.

## 1.2.2 — 2026-09-18

- Replaced the retired AgentSession `checkpoint-lease/*` client routes with the canonical
  harness-neutral `runtime-lease/*` acquire, renew, and release routes.
- Aligned internal A2A task-dispatch and caller-delivery signals with the current Django contract.

## 1.2.1 — 2026-09-17

- Added durable local-mode A2A Message and Task execution across REST, JSON-RPC, and SSE, backed by
  the workspace SQLite store without creating platform Agent or AgentSession records.
- Narrowed `local_mode_capability_unsupported` to agent-targeted responses and internal/platform
  routing features that genuinely require registered identity or callback delivery.
- Expanded the packaged local-development skill and public documentation with the exact local A2A
  persistence, identity, restart, polling, and unsupported-routing boundaries.

## 1.2.0 — 2026-09-17

- Unified managed and local backend selection on the established `MAINSEQUENCE_ENDPOINT`
  environment variable.
- Added explicit `ms-tau skills sync`, `list`, and `path` commands with an atomically managed
  `.agents/skills/ms_tau_sdk/` namespace and version provenance.
- Moved TAU repository integration, local development, project customization, and A2A host-adapter
  guidance into skills packaged with the SDK.
- Defined the hard ownership boundary that leaves platform ontology and canonical A2A semantics in
  Django while keeping SDK-versioned implementation mechanics in this distribution.

## 1.1.1 — 2026-09-17

- Fixed local-mode provider hydration to preserve the canonical credential envelope consumed by
  the shared Tau runtime parser, so a hydrated credential reaches provider execution.
- Added focused parser coverage and an end-to-end local chat regression through the real Tau
  session runtime.

## 1.1.0 — 2026-09-17

- Added authenticated local development mode: workspace-scoped SQLite Tau state, lazy local chat
  sessions, explicit provider/model selection, dependency-free user-JWT refresh, real Main
  Sequence provider hydration and MCP, and typed rejection of registered-agent orchestration.
- Added a release guard that forbids a `mainsequence` distribution dependency or package import;
  the local authentication boundary is environment variables plus the public HTTP refresh API.

## 1.0.0 — 2026-09-16

- Established Main Sequence TAU SDK as the `ms-tau-sdk` Python distribution, `ms_tau_sdk`
  namespace, and `ms-tau` process command.
- Made every process project-workspace-bound and exposed `create_app` plus `TauSDKSettings` as the
  public construction surface.
- Preserved the existing Main Sequence transports, runtime-credential client, provider hydration,
  durable and sessionless Tau execution, persistence, streaming, A2A, MCP, and lifecycle behavior.
- Adopted Tau-native project `.tau` configuration and project-owned extensions with no parallel SDK
  prompt configuration or deployment enable flag.
- Removed container, Compose, Kubernetes, image, overlay, wheelhouse, and optional web/search/video
  tool ownership from this project.
- Added allowlisted wheel/sdist verification, isolated installed-process checks, release checksums,
  dependency/provenance metadata, build attestation, and PyPI trusted-publishing automation.
- Declared the reviewed Python API, command, settings, project-configuration, and tested wire
  contracts as the first stable compatibility surface.
- Removed residual provider-specific image-build and push material from the historical archive.

The changelog for the retired deployment project is retained as
[historical context](./docs/history/astro/CHANGELOG.md).
