# Structured Logging Contract

This page defines the structured JSON log contract for Astro services.

Astro emits both:

- human-readable runtime diagnostics such as `[astro-stream] OUT ...`
- structured JSON logs for machine parsing and operational tracing

Only the structured JSON logs on this page are the stable machine-readable contract.

## Scope

This contract currently covers these structured-log services:

- `astro-stream`
- `astro-checkpoint-sidecar`
- `agent-registration`

## Shared JSON shape

Every structured log entry is a single JSON object written on one line.

Canonical top-level shape:

```json
{
  "severity": "INFO",
  "time": "2026-05-07T12:34:56.789Z",
  "component": "astro-stream",
  "event": "checkpoint_marker_written",
  "message": "Checkpoint marker written for sidecar flush.",
  "session_id": "52",
  "data": {
    "session_id": "52",
    "threadId": "52",
    "agentSessionUid": "session_52_uid",
    "reason": "finish"
  }
}
```

Field contract:

- `severity`
  - one of `DEBUG`, `INFO`, `WARNING`, `ERROR`
- `time`
  - ISO-8601 UTC timestamp
- `component`
  - stable service name
- `event`
  - stable machine-facing event key
- `message`
  - short human-readable summary
- `session_id`
  - canonical runtime session id when the event is session-scoped
- `data`
  - event-specific payload

## Session id normalization

For session-scoped logs, Astro must emit top-level:

```json
"session_id": "<runtime_session_uid>"
```

The shared logger normalizes `session_id` automatically from any of these payload keys:

- `session_id`
- `sessionUid`
- `sessionKey`
- `runtime_session_uid`
- `runtimeSessionUid`
- `agent_session_uid`
- `agentSessionUid`

Rules:

- callers should still pass a session-like identifier in the payload when the event is about a
  session
- the logger copies that into canonical top-level `session_id`
- the normalized `session_id` is also preserved inside `data`
- startup and process-global logs may omit `session_id`

## Emission rules

Warnings and errors always emit.

`INFO` emission rules:

- normal non-checkpoint `INFO` logs emit by default
- checkpoint `INFO` logs are suppressed by default to reduce noise
- checkpoint `INFO` logs emit when:

```bash
ASTRO_LOG_CHECKPOINT_INFO=1
```

This suppression applies to both:

- `astro-stream` checkpoint info logs
- `astro-checkpoint-sidecar` routine checkpoint info logs

## Service: `astro-stream`

### Purpose

`astro-stream` is the HTTP stream server and runtime wrapper.

It emits structured logs for:

- stream request lifecycle
- session hydration and resume
- model discovery and model binding
- provider credential hydration/flush
- checkpoint lease and restore
- Pi launch and runtime write failures
- A2A request normalization and session attachment

### Component value

Every structured log from this service uses:

```json
"component": "astro-stream"
```

### Common payload fields

Session-scoped `astro-stream` events commonly include:

- `session_id`
- `threadId`
- `agentSessionUid`
- `agentType`
- `userId`
- `projectId`
- `cwd`
- `checkpointVersion`
- `bundleHash`

Request-scoped events may also include:

- `path`
- `executionMode`
- `fixedAgentType`
- `status`
- `error`

### Event catalog

Current emitted `astro-stream` event keys:

- `available_models_pi_registry_filtered_out`
- `available_models_request_failed`
- `available_models_request_started`
- `available_models_request_succeeded`
- `available_models_user_id_missing`
- `backend_checkpoint_hydration_attached`
- `backend_checkpoint_hydration_local_session_incomplete`
- `backend_session_hydration_attached`
- `backend_session_hydration_attempt`
- `backend_session_hydration_fetch_failed`
- `backend_session_hydration_invalid_uid`
- `backend_session_hydration_invalid_payload`
- `backend_session_hydration_missing_agent_uid`
- `backend_session_hydration_not_found`
- `backend_session_hydration_succeeded`
- `backend_session_hydration_unavailable`
- `backend_session_hydration_wrong_user`
- `backend_session_hydration_missing_agent_type`
- `chat_history_blocked_during_active_stream`
- `chat_history_checkpoint_bundle_invalid`
- `chat_history_checkpoint_fetch_failed`
- `chat_history_checkpoint_invalid_session_history`
- `chat_history_checkpoint_response_invalid`
- `chat_history_ignored_transient_checkpoint_error_cache`
- `chat_history_rebuilt_from_checkpoint`
- `chat_history_sanitized_prompt_wrapper`
- `checkpoint_finalization_recovered`
- `checkpoint_history_restore_release_error`
- `checkpoint_history_restore_release_failed`
- `checkpoint_latest_missing_restore_fallback`
- `checkpoint_launch_ignored_local_finalizing_state`
- `checkpoint_lease_failed`
- `checkpoint_lease_renew_error`
- `checkpoint_lease_renew_failed`
- `checkpoint_lease_renew_stale_manifest`
- `checkpoint_lease_reused`
- `checkpoint_marker_skipped_no_lease`
- `checkpoint_marker_write_failed`
- `checkpoint_marker_written`
- `checkpoint_prelaunch_invalid_history_release_failed`
- `checkpoint_restore_completed`
- `checkpoint_restore_failed`
- `checkpoint_restore_failed_release_failed`
- `checkpoint_restore_invalid_history_release_failed`
- `checkpoint_restore_skipped_current`
- `checkpoint_restore_started`
- `history_reasoning_annotation_write_failed`
- `model_provider_user_id_missing`
- `pi_child_stderr`
- `pi_launch_model_ready`
- `pi_launch_without_model`
- `pi_session_history_repaired`
- `pi_session_validation_failed`
- `provider_credentials_flush_failed`
- `provider_credentials_flushed`
- `provider_credentials_hydrate_failed`
- `provider_credentials_hydrated`
- `provider_credentials_shutdown_flush_failed`
- `provider_credentials_shutdown_flush_started`
- `provider_credentials_shutdown_flushed`
- `read_endpoint_checkpoint_hydration_failed`
- `read_endpoint_history_projection_failed`
- `read_endpoint_session_hydrated_from_checkpoint`
- `request_model_ignored_session_first`
- `request_session_metadata_attached`
- `session_cancel_request_failed`
- `session_cancellation_force_kill`
- `session_cancellation_started`
- `session_model_binding_missing`
- `session_model_binding_refreshed_from_runtime`
- `session_model_binding_repaired_from_session_history`
- `session_model_binding_resolved`
- `session_run_already_active`
- `stream_client_detached`
- `stream_client_write_failed`
- `stream_done_write_failed`

### Contract notes

- `event` is the primary machine-facing key. Consumers should key off `component + event`, not the
  free-form `message`.
- `message` may be reworded without changing the contract.
- session-scoped events should carry enough identifiers to correlate with the active runtime
  session, especially `session_id`, `threadId`, and `agentSessionUid` when available.

## Service: `astro-checkpoint-sidecar`

### Purpose

`astro-checkpoint-sidecar` watches session files, builds checkpoint bundles, flushes them to the
backend, uploads session insights, and releases checkpoint leases after terminal flushes.

### Component value

Every structured log from this service uses:

```json
"component": "astro-checkpoint-sidecar"
```

### Common payload fields

Session-scoped sidecar events commonly include:

- `session_id`
- `agent_session_uid`
- `reason`
- `checkpoint_version`
- `bundle_hash`
- `lease_expires_at`
- `latency_ms`
- `dirty_age_ms`

Watcher and process-global events may also include:

- `label`
- `dir`
- `signal`
- `shutdown_timeout_ms`

### Event catalog

Current emitted `astro-checkpoint-sidecar` event keys:

- `checkpoint_flush_error`
- `checkpoint_flush_local_validation_failed`
- `checkpoint_flush_rejected`
- `checkpoint_flush_skipped_expired_lease`
- `checkpoint_flush_skipped_marker_missing_lease`
- `checkpoint_flush_skipped_stale_marker`
- `checkpoint_flush_skipped_unmanaged_session`
- `checkpoint_flush_started`
- `checkpoint_lease_release_failed`
- `checkpoint_lease_released`
- `checkpoint_marker_invalid`
- `checkpoint_marker_read_failed`
- `checkpoint_restore_observed`
- `checkpoint_sidecar_metrics`
- `checkpoint_sidecar_started`
- `checkpoint_sidecar_stopped`
- `checkpoint_sidecar_watch_started`
- `session_insights_upload_failed`
- `session_insights_upload_skipped`
- `session_insights_uploaded`

### Severity contract

Current sidecar severity policy:

- `ERROR`
  - `checkpoint_flush_error`
- `WARNING`
  - `checkpoint_flush_rejected`
  - `checkpoint_flush_skipped_expired_lease`
  - `checkpoint_flush_skipped_marker_missing_lease`
  - `checkpoint_flush_skipped_stale_marker`
  - `checkpoint_flush_skipped_unmanaged_session`
  - `checkpoint_lease_release_failed`
  - `checkpoint_marker_invalid`
  - `checkpoint_marker_read_failed`
  - `session_insights_upload_failed`
  - `session_insights_upload_skipped`
- `INFO`
  - all other current sidecar events

Routine sidecar `INFO` logs are suppressed by default unless `ASTRO_LOG_CHECKPOINT_INFO=1`.

## Service: `agent-registration`

### Purpose

`agent-registration` emits structured logs for backend agent-session lookup and hydration helpers.

### Component value

Every structured log from this service uses:

```json
"component": "agent-registration"
```

### Common payload fields

Common payload fields include:

- `session_id` when the event is session-scoped
- `agentSessionUid`
- `endpoint`
- `status`
- `error`
- `candidateEndpoints`

### Event catalog

Current emitted `agent-registration` event keys:

- `backend_session_fetch_attempt`
- `backend_session_fetch_endpoint_not_found`
- `backend_session_fetch_invalid_uid`
- `backend_session_fetch_missing_auth_headers`
- `backend_session_fetch_not_found`
- `backend_session_fetch_rejected`
- `backend_session_fetch_succeeded`

### Contract notes

- events in this component are mostly backend-fetch lifecycle events
- when an event is about a specific backend AgentSession, the normalized top-level `session_id`
  comes from the passed `agentSessionUid`

## Startup-only structured logs

The stream startup wrapper also emits `astro-stream` structured logs before any session exists:

- `workspace_analysis_skill_ready`
- `orchestrator_runtime_ready`

These are valid structured logs and intentionally may omit `session_id`.

## Plain logs that are not part of this contract

These are still expected, but they are not the stable structured-log contract:

- `[astro-stream] OUT ...`
- `[astro-stream] STDERR ...`
- `[astro-http] ...`

They remain useful for local debugging and live operator inspection.

## Non-goals

This contract does not guarantee:

- a closed global enum of every future `event` value forever
- that every process-global startup log has a session id
- that human-readable `[astro-*]` lines are parse-stable

## Related files

- [structured-logging.ts](/Users/jose/code/MainSequenceServerSide/astro/pi/extensions/shared/structured-logging.ts)
- [server.ts](/Users/jose/code/MainSequenceServerSide/astro/interface/stream/server.ts)
- [session_checkpoint_sidecar.ts](/Users/jose/code/MainSequenceServerSide/astro/scripts/session_checkpoint_sidecar.ts)
- [agent-registration.ts](/Users/jose/code/MainSequenceServerSide/astro/pi/extensions/shared/agent-registration.ts)
