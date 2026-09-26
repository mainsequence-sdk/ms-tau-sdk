# Changelog

## 1.2.9 — 2026-09-25

- Fixed managed A2A Task creation and continuation to send dictionary-valued message extensions
  to Django. Missing extensions now become `{}`; unsupported shapes return HTTP 400 before a
  backend Task mutation, instead of surfacing Django validation as HTTP 502. (Issue #40.)
- Added Tau Board's Agent view for readable effective Agent Card inspection, source-grouped tool
  catalog inspection, project extension diagnostics, and bounded registered-entry source viewing.
- Added an explicit local project-tool workbench with schema-generated inputs, authoritative
  validation, short-lived single-use confirmation, exact loaded-tool execution, streamed bounded
  results, timeout and cancellation. It does not call the model or write conversation/Task history;
  project code still has its normal real side effects.
- Added creation and completion/status timing to the A2A Task view. Completion uses the standard
  A2A `task.status.timestamp`; creation is joined from Tau's existing local SQLite Task state, so
  the wire contract remains standard.

## 1.2.8 — 2026-09-24

- Added independent `TAU_EXCLUDE_BASE_TOOLS` and `TAU_EXCLUDE_MAINSEQUENCE_MCP` process settings.
  Deployments can expose only project extension tools plus the two required A2A Task controls.
  The runtime skips MCP connection and resource guidance when excluded, rejects project overrides
  of the Task controls, and checks the effective catalog on load and after extension reload.
- Documented the settings for managed workflow `env_vars` and local runtimes in ADR 0011, the
  reference guides, and the version-matched `tau-project-customization` skill.

## 1.2.7 — 2026-09-21

- Bundle the local Tau Board command, Python package, and UI assets in the `ms-tau-sdk` wheel.
  `ms-tau-sdk[tau-board]` now selects Board's tested dependency bounds without requiring a
  second PyPI project. The Board remains a separate local process. SDK releases no longer wait
  for an unpublished `ms-tau-board` distribution.
- A merge to `main` is the release. The release workflow runs on the merge instead of on a
  hand-pushed tag: it publishes the version `pyproject.toml` declares, creates the tag `vX.Y.Z` and
  the GitHub release after the upload, and brings the release merge and the next patch number to
  `development` in one push, so no `X.Y.Z.devN` can follow the final `X.Y.Z`. A merge that still
  declares a released version, or whose tag already names another commit, publishes nothing.
- Stopped discarding the only diagnostic a message-less provider failure carries. When Tau ends a
  turn with `stopReason: "error"` and no `errorMessage`, the status code from the `provider_error`
  diagnostic is composed into the message the user sees (`Provider error (HTTP 402)`) instead of
  the bare `Provider error` constant, `agent.model.failed` now emits `status_code`, and the
  assistant-ui `error` frame carries `status` and `error_code`. An unpaid account and a
  misconfigured credential are distinguishable again, in the stream and in the logs. The
  message/body split is unchanged: the provider's message is still forwarded verbatim and its raw
  response body still never leaves the process.

## 1.2.6 — 2026-09-21

- Made `pyproject.toml` the only source of the version. `development` declares the release being
  worked toward, development releases are that number with `.devN`, and the final release is the
  same number as a tag on `main`. The repository used to say `1.2.5` while it published
  `1.2.6.devN`, and tagging `v1.2.6` then failed against `pyproject.toml`. PyPI is now read only as
  a guard that refuses a development build of an already released version, and the release
  workflow raises the patch number on `development` after it publishes.
- Stopped sending `execution_context` in local-mode provider hydration. The backend never declared
  the field on `POST /api/v1/model-provider-credentials/hydrate/` and dropped it silently;
  authentication already distinguishes local development from runtime execution, so the request
  carries no execution-context discriminator.
- Moved durable Tau runtime state out of the installed package. Built-in extension state, provider
  credentials, project trust, and agent-call diagnostics now live under
  `MAINSEQUENCE_TAU_STATE_ROOT/<workspace-hash>`, defaulting to an XDG-style user state directory,
  instead of `ms_tau_sdk/resources/`. An installed wheel no longer writes to its own site-packages
  directory, so a read-only install works, and a test run no longer leaves runtime files in `src`
  for the next build to package. Managed-mode and local-mode behavior is otherwise unchanged.
- Made the release gate reject runtime residue. `scripts/verify_distribution.py` now holds an
  explicit allowlist of the entries and file types the package ships, and refuses any `state/`
  directory, so this class of leak fails the gate instead of shipping.
- Adopted one branch and release standard. A final release is a `vX.Y.Z` tag on `main`, and the
  publish workflow refuses a tag whose commit `main` does not contain. Every push to `development`
  publishes one `X.Y.Z.devN` release automatically, after the same quality gate as `quality.yml`
  and only when it passes. `pip` and `uv` ignore development releases unless one is pinned exactly.
- Made `pyproject.toml` the only file that declares the version. The consumer fixture resolves the
  SDK from the checkout instead of naming a version, and the contract tests read the declared
  version rather than repeating it.

## 1.2.5 — 2026-09-19

- Persist local-mode operational events as workspace-scoped, rotated, private JSON Lines while
  retaining console sinks and leaving managed-mode logging unchanged.
- Preserve exception types and traceback locations without persisting raw exception messages.

## 1.2.4 — 2026-09-19

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
