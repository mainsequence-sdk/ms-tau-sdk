# Changelog

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
