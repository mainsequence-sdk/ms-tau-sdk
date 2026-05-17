# Changelog

## 1.0.0 - 2026-05-17

### Major Runtime Contract Upgrade

- Introduced Astro runtime profiles so local behavior is selected by the deployed runtime profile and project attachment, while backend identity remains keyed by `agent_type`.
- Unified the base prompt contract into `.pi/APPEND_SYSTEM.md` and removed the standalone `mainsequence-project-executor` prompt overlay.
- Removed project-worker behavior from generic specialist prompt discovery; fixed project workers now use deterministic shared-prompt plus project-attached context.
- Made fixed-worker model binding session-first: backend/session model authority is respected, and delegating sessions cannot override target worker model policy through unrelated request state.
- Preserved durable A2A envelope and provenance metadata independently from prompt/runtime role, including target backend session identity.
- Normalized checkpoint metadata, session metadata, history hydration metadata, and session insights around backend `agent_type` and backend `AgentSession` identity.
- Converged repo-owned orchestrator and local project-worker runtime filesystem contracts on the `/home/jovyan` Astro data/config/Pi layout with shared `/session-state` sidecar paths.
- Retained `Dockerfile.remote-worker` as the canonical external project-executor image reference while aligning local Compose, Kubernetes examples, and docs with the unified contract.
- Removed Astro-owned project setup/finalize wrapper scripts in favor of the canonical Main Sequence project/runtime flow.
- Updated image/version tagging docs to use `ms-sdk-<version>` for Main Sequence SDK version tags.

### Compatibility Notes

- Backend-visible identities remain `astro-orchestrator` and `mainsequence-project-executor`.
- Request/session identity remains `agentType` / `agent_type`; removed aliases such as `agentName` are not reintroduced.
- Existing local metadata does not require a one-time migration; the runtime reads camel-case and snake-case backend identity fields and writes normalized forms going forward.
