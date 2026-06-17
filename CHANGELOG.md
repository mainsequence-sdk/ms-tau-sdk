# Changelog

## 2.0.6 - 2026-06-16

### A2A Cancellation Lifecycle

- Changed legacy A2A chat disconnect handling so abandoned queued requests are skipped instead of blocking same-session retries.
- Changed active A2A client disconnects and SSE write failures to cancel the runtime turn instead of continuing detached like UI chat.
- Added optional request-scoped A2A turn timeout fields so callers can choose a watchdog per legacy A2A chat request; omitted or zero disables the watchdog.
- Ensured warm-runner cancellation resolves the active turn and releases the queue when the underlying Pi RPC runner exits.
- Added structured queue/cancellation diagnostics for queued, started, skipped, disconnected, and timed-out A2A turns.

## 2.0.5 - 2026-06-16

### A2A Runtime Performance And Output Contracts

- Added cached session capability materialization and scoped provider credential reuse for same-session runtime turns.
- Added warm Pi RPC runners for legacy A2A chat so same-session A2A calls can avoid repeated cold process startup.
- Added runtime-enforced A2A output options for `omit_reasoning` / `omitReasoning`, strict JSON `response_format` / `responseFormat`, and `json_repair` / `jsonRepair`.
- Buffered strict JSON assistant text until validation succeeds, emits canonical JSON on success, and emits `a2a_invalid_json_response` without persisting invalid text when validation and repair fail.
- Documented ADR 33 and ADR 34, warm-runner and strict JSON repair environment knobs, and A2A request examples.
- Added focused tests for cached preflight, scoped provider credential cache behavior, strict JSON validation/repair, reasoning suppression, non-strict streaming, and warm/cold A2A output paths.

## 2.0.4 - 2026-06-15

### Runtime Identity Vocabulary

- Changed Astro runtime profile values to use backend agent-type vocabulary: `astro-orchestrator` and `project-executor`.
- Removed `project_worker` as a public runtime-profile name while keeping `ASTRO_EXECUTION_MODE=remote_project_worker` as legacy topology metadata.
- Documented the two deployment identities, fixed `project-executor` request validation, and matching sidecar filesystem expectations.
- Expanded deployment identity docs across the README, interface request/error contract, quickstart, and persistent-state guidance so fixed-worker identity and sidecar path requirements are discoverable from operational docs.
- Kept existing `ASTRO_EXECUTION_MODE=remote_project_worker` deployments backward compatible as topology metadata while preserving `ASTRO_FIXED_AGENT_TYPE=project-executor` as the fixed executor identity source.

## 2.0.3 - 2026-06-14

### Session Capability Bindings

- Added backend capability binding support for session-local skill overlays.
- Materialized enabled non-repository session skill bindings under `/session-state/session-assets/<agent_session_uid>/.agents/skills/`.
- Skipped repository-sourced capability bindings so fixed agent capabilities are not duplicated into session state.
- Recorded materialization counts, skipped classifications, and paths in structured logs and session metadata for checkpointing.

## 2.0.2 - 2026-06-13

### Checkpoint Lease Lifecycle

- Replaced the sidecar's epoch-based released-lease sentinel with explicit released lease metadata.
- Deferred dirty checkpoint flushes when the local manifest has a missing, released, or expired lease instead of dropping the dirty state.
- Moved chat launch persistence so turn metadata, thread binding, and user-message history are written after checkpoint lease acquisition succeeds.

## 2.0.0 - 2026-05-22

### Strict Backend UID Contract

- Replaced backend resource lookup identity from deprecated backend `id` values to backend `uid` values across session hydration, checkpoint, cancellation, A2A envelope, and provider-credential runtime helpers.
- Changed the active request contract to require `runtime_session_uid` for real chat and A2A attach, with backend session serializers expected to expose `uid` instead of `id`.
- Updated runtime response surfaces to emit backend uid terminology, including `X-Agent-Uid`, `X-Agent-Session-Uid`, `agent_uid` stream chunks, and `sessionUid` / `agentSessionUid` JSON response fields on control-plane endpoints.
- Aligned active docs, session insights metadata, and ADR 31 with the strict uid cutover, while keeping `agent_type` and `agent_unique_id` distinct from backend resource identity.

## 1.0.2 - 2026-05-17

### Strict Project Executor Identity

- Cut the project executor runtime over to the canonical backend `agent_type` value `project-executor`.
- Updated fixed-worker deployment env, runtime profile detection, request/session validation, A2A examples, and docs to use `project-executor` consistently.
- Removed the obsolete standalone project executor prompt overlay from `.pi/agents`; project-attached behavior now comes from `.pi/APPEND_SYSTEM.md` plus `ASTRO_FIXED_AGENT_TYPE=project-executor`.
- Verified the removed executor identity has no remaining code, docs, prompt, or filename references.

## 1.0.0 - 2026-05-17

### Major Runtime Contract Upgrade

- Introduced Astro runtime profiles so local behavior is selected by the deployed runtime profile and project attachment, while backend identity remains keyed by `agent_type`.
- Unified the base prompt contract into `.pi/APPEND_SYSTEM.md` and removed the standalone `project-executor` prompt overlay.
- Removed project-worker behavior from generic specialist prompt discovery; fixed project workers now use deterministic shared-prompt plus project-attached context.
- Made fixed-worker model binding session-first: backend/session model authority is respected, and delegating sessions cannot override target worker model policy through unrelated request state.
- Preserved durable A2A envelope and provenance metadata independently from prompt/runtime role, including target backend session identity.
- Normalized checkpoint metadata, session metadata, history hydration metadata, and session insights around backend `agent_type` and backend `AgentSession` identity.
- Converged repo-owned orchestrator and local project-worker runtime filesystem contracts on the `/home/jovyan` Astro data/config/Pi layout with shared `/session-state` sidecar paths.
- Retained `Dockerfile.remote-worker` as the canonical external project-executor image reference while aligning local Compose, Kubernetes examples, and docs with the unified contract.
- Removed Astro-owned project setup/finalize wrapper scripts in favor of the canonical Main Sequence project/runtime flow.
- Updated image/version tagging docs to use `ms-sdk-<version>` for Main Sequence SDK version tags.

### Compatibility Notes

- Backend-visible identities remain `astro-orchestrator` and `project-executor`.
- Request/session identity remains `agentType` / `agent_type`; removed aliases such as `agentName` are not reintroduced.
- Existing local metadata does not require a one-time migration; the runtime reads camel-case and snake-case backend identity fields and writes normalized forms going forward.
