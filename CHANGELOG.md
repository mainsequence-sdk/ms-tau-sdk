# Changelog

## 1.2.0 — 2026-09-17

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
