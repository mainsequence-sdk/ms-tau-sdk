# Changelog

## Unreleased

### Retired centralized AgentCapability registry

- Removed Astro's AgentCapability/session-binding DTOs, client routes, materializer, cache scan,
  session asset root, and related deployment plumbing.
- Removed capability hashes and capability payloads from Tau bootstrap, resume snapshot restore,
  and snapshot upload behavior.
- Aligned the coordinated Django ADR-032 cutover by requiring Tau bootstrap v3, resume snapshot v2,
  and snapshot schema v2. Astro 4.0.22 retained the obsolete runtime-version gates and is
  superseded by 4.0.23.
- Kept repository-native `.agents/skills` and `.tau/extensions`, process-scoped Main Sequence MCP
  platform skills/tools, A2A Agent Card capabilities, and backend `runtime_capabilities` protocol
  negotiation as separate authoritative concepts.

### CodeRepository Tau extensions

- CodeRepository Executor images now enable Tau-native project extensions from
  `/workspace/.tau/extensions`; generic Astro runtimes remain disabled by default.
- Executor images publish `PYTHONPATH=/workspace/src:/workspace`, allowing extension adapters to
  import both `src`-layout packages and flat repository modules without editing `sys.path`.
- Runtime health, `runtime_info`, and structured session-load logs now report extension,
  project-tool, diagnostic, and error counts; deterministic effective tool-catalog digests; and
  sanitized Tau extension diagnostics.
- Added final-image verification and unit fixtures covering project and sibling imports, tool
  registration, extension-over-base precedence, hook execution, and durable Tau tool invocation.

### Current-turn platform action discipline

- Explicit requests for supported actions now require a fresh tool attempt in the current turn.
  Astro may not reuse an earlier failure, stale lookup, or prior runtime state as a present blocker,
  and may report a blocker only from the current tool result.
- Explicit action requests count as authorization when the underlying workflow does not require a
  separate confirmation. Astro refreshes required state before executing and does not invent
  missing permissions or alternate execution-context requirements.

### Catalog-driven caller-session provenance

- Main Sequence MCP Tool `_meta` is now the source of truth for private caller-session proof:
  every operation advertising `mainsequence.ai/requires-caller-session-proof/v1: true` receives
  the active runtime session lease proof under `mainsequence.ai/caller-session-proof/v1`.
- Removed Astro's hard-coded A2A-only metadata mapping. Both `a2a.send_message` and
  `agent.update_runtime` now use the same generic projection path, and marked tools fail closed at
  session setup if trusted host proof is unavailable.

### Chat stream speaks the assistant-stream tool vocabulary

- `POST /api/chat` now emits `tool-call-start` / `tool-call-delta` / `tool-call-end` when the model
  finishes a tool call and `tool-result` when the tool has run, instead of the
  `tool-input-available` / `tool-output-delta` / `tool-output-available` names that no consumer
  decoded. Tool cards, including which Main Sequence MCP tool ran, can now render in the Command
  Center chat.
- Tau `turn_start` / `turn_end` become `start-step` / `finish-step` (with the turn's finish reason),
  so clients can count and label the steps of a run.
- A `tool-result` for a call the stream never announced is preceded by a synthesized announcement,
  and `tool_execution_update` is no longer forwarded (the final result carries everything).

### Outbound A2A message delivery

- Removed Astro's dedicated outbound A2A tool implementation. The existing generic MCP projection
  now exposes Django MCP's canonical `a2a.send_message` operation as
  `mainsequence__a2a_send_message`; Django owns target-session allocation, runtime access, the
  `ROLE_REQUESTER` envelope, response validation, and send auditing in one place.
- Generic host plumbing injects the active caller `AgentSession.uid`, runtime lease holder, and
  lease token as private MCP request metadata for that operation. None is model-selectable; Django
  validates the proof against the authenticated coding-agent service and exact active lease before
  allocating a target session.
- Runtime URLs, runtime bearer credentials, and caller lease proof stay out of model-authored
  arguments, target messages, tool results, and logs.
- Django's canonical sender records each attempt against the authenticated User, with verified
  caller Agent/session/service/harness provenance for Agent sends, tracked MCP client metadata for
  User sends, the target Agent/session, message and correlation IDs, and a safe terminal or
  ambiguous outcome. Message content and credentials are not persisted.
- Corrected the outbound and inbound A2A v1 role codec, modelled roles internally as requester and
  responder directions rather than principal identity, and removed obsolete v0.3 `kind`
  discriminators from v1 Message and Task responses.
- Django follows backend-declared transient runtime-interaction states within its bounded MCP
  execution budget, enforces `runtime_interaction.can_submit` before delivery, and returns the
  backend notice when submission is blocked.
- Replaced the identity-oriented A2A role values with the canonical Main Sequence wire directions
  `ROLE_REQUESTER` and `ROLE_RESPONDER`; sender and receiver no longer translate through
  `ROLE_USER` or `ROLE_AGENT`.
- Documented the model-visible host-tool contract separately from the private caller proof and
  runtime wire envelope: callers supply `agent_uid`, `message`, and exactly one session selector;
  Django constructs the `ROLE_REQUESTER` request itself.

### Gateway-verified caller identity (ADR-0043 cutover-bound)

- Protected message routes (`POST /api/chat`, A2A `message:send`, `message:stream`, the JSON-RPC
  message methods, and task turns) require the gateway's `X-Caller-Kind` and matching
  `X-User-UID` / `X-Caller-Agent-UID` / `X-Caller-Coding-Agent-Service-UID` /
  `X-Caller-Agent-Session-UID` headers, and reject a request before any session work with
  HTTP 403 `{"detail": "Missing or invalid caller identity headers.", "code":
  "runtime_caller_identity_invalid"}` (a JSON-RPC error carrying the same code on `/api/a2a/rpc`).
- Each user turn's provenance custom entry now carries `actorKind`, `actorUid`, `actorName`
  (humans only), and `callerAgentSessionUid`, with `origin` taken from `X-Caller-Kind` instead of
  the route. Request logs bind `caller_kind` and `caller_agent_uid`.
- This image is bound to the tdag-django ADR-0043 coordinated hard cutover: deploy it only as
  step 4 of that cutover, after Django and both coding-agent gateways forward the headers.
  Deployed alone it rejects every message request by design.

### Lean Python Runtime ABI

- Replaced the standalone Tau image's floating slim/Jovyan layout with the
  digest-pinned Python 3.13 Bookworm base, one `/opt/venv` environment, and the
  non-root `appuser` identity (`10000:10000`).
- Rewrote the CodeRepository Executor overlay to consume `/workspace`, `/app`,
  `/home/appuser`, and `/session-state` directly, removing every `NB_*`,
  `SKEL_APP_DIR`, `APP_DIR`, and Jovyan dependency.
- Added fail-closed base-ABI, Git-provenance, wheel-integrity, filesystem
  ownership, Compose, and Kubernetes security-context checks.

### CodeRepository Runtime Ontology

- Replaced the repository executor runtime contract with canonical-only CodeRepository settings,
  executor and bundle discriminators, MCP examples, structured log fields, and deployment
  artifacts under ADR-046. Superseded repository runtime names are no longer accepted.

## 2.0.7 - 2026-06-19

### General Pi Runtime Package Boundary

- Added ADRs and implementation planning for separating Astro Core from Main Sequence-specific Pi resources and backend adapter concerns.
- Introduced the Main Sequence adapter-owned Pi resource overlay under `adapters/mainsequence/pi-overlay`, including Main Sequence prompts, tools, project policy, and SDK-owned skill discovery.
- Moved SDK skill delivery in the adapter overlay to a `resources_discover` Pi extension that delegates `.agents/skills` seeding to the installed Main Sequence SDK/CLI instead of duplicating `agent_scaffold` copy logic in Astro.
- Removed the legacy orchestrator bootstrap path that copied only `a2a_communication` and `command_center/workspace_analysis`; the package hook now resolves the installed SDK skill root and exposes the full SDK skill tree through `.agents/skills/mainsequence`.
- Changed the package-owned Main Sequence skill discovery extension to seed `.agents/skills/mainsequence` when Pi loads the extension, so the runtime cwd has `.agents` before model-visible skill checks instead of waiting for a later discovery event.
- Removed Main Sequence SDK skill slugs from Astro Core A2A instructions so package-provided A2A guidance stays in the active Pi package/runtime context.
- Reduced root `pi/` to Astro runtime-owned resources and removed Main Sequence product skills, prompts, project-policy hooks, and obsolete repo-maintenance/specialist resources from Astro Core.
- Reorganized runtime entrypoints and support utilities out of the legacy `scripts/` folder into `bin/`, `runtime/`, `adapters/`, and `tools/`.
- Updated docs, launch configuration, and A2A testing guidance for the standard A2A workflow and the moved live stress/debug client.

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

- Changed Astro runtime profile values to use backend agent-type vocabulary: `astro-orchestrator` and `code-repository-executor`.
- Removed the retired repository-worker profile and its topology environment metadata; runtime identity now comes only from canonical backend Agent types and CodeRepository attachment.
- Documented the two deployment identities, fixed `code-repository-executor` request validation, and matching sidecar filesystem expectations.
- Expanded deployment identity docs across the README, interface request/error contract, quickstart, and persistent-state guidance so fixed-worker identity and sidecar path requirements are discoverable from operational docs.
- Preserved `ASTRO_FIXED_AGENT_TYPE=code-repository-executor` as the fixed executor identity source without a legacy repository-worker fallback.

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
- Updated runtime response surfaces to emit backend uid terminology, including `X-Agent-Session-Uid`, `agent_uid` stream chunks, and `sessionUid` / `agentSessionUid` JSON response fields on control-plane endpoints.
- Removed semantic Agent identity from the active runtime contract; Astro now relies on backend Agent and AgentSession `uid` values plus `agent_type`.

## 1.0.2 - 2026-05-17

### Strict CodeRepository Executor Identity

- Cut the code repository executor runtime over to the canonical backend `agent_type` value `code-repository-executor`.
- Updated fixed-worker deployment env, runtime profile detection, request/session validation, A2A examples, and docs to use `code-repository-executor` consistently.
- Removed the obsolete standalone code repository executor prompt overlay from `.pi/agents`; code-repository-attached behavior now comes from `.pi/APPEND_SYSTEM.md` plus `ASTRO_FIXED_AGENT_TYPE=code-repository-executor`.
- Verified the removed executor identity has no remaining code, docs, prompt, or filename references.

## 1.0.0 - 2026-05-17

### Major Runtime Contract Upgrade

- Introduced Astro runtime profiles so local behavior is selected by the deployed runtime profile and code repository attachment, while backend identity remains keyed by `agent_type`.
- Unified the base prompt contract into `.pi/APPEND_SYSTEM.md` and removed the standalone `code-repository-executor` prompt overlay.
- Removed CodeRepository-worker behavior from generic specialist prompt discovery; fixed code repository workers now use deterministic shared-prompt plus code-repository-attached context.
- Made fixed-worker model binding session-first: backend/session model authority is respected, and delegating sessions cannot override target worker model policy through unrelated request state.
- Preserved durable A2A envelope and provenance metadata independently from prompt/runtime role, including target backend session identity.
- Normalized checkpoint metadata, session metadata, history hydration metadata, and session insights around backend `agent_type` and backend `AgentSession` identity.
- Converged repo-owned orchestrator and local CodeRepository-worker runtime filesystem contracts on the `/home/jovyan` Astro data/config/Pi layout with shared `/session-state` sidecar paths.
- Retained `Dockerfile.remote-worker` as the canonical external code-repository-executor image reference while aligning local Compose, Kubernetes examples, and docs with the unified contract.
- Removed Astro-owned CodeRepository setup/finalize wrapper scripts in favor of the canonical Main Sequence CodeRepository/runtime flow.
- Updated image/version tagging docs to use `ms-sdk-<version>` for Main Sequence SDK version tags.

### Compatibility Notes

- Backend-visible identities remain `astro-orchestrator` and `code-repository-executor`.
- Request/session identity remains `agentType` / `agent_type`; removed aliases such as `agentName` are not reintroduced.
- Existing local metadata does not require a one-time migration; the runtime reads camel-case and snake-case backend identity fields and writes normalized forms going forward.
