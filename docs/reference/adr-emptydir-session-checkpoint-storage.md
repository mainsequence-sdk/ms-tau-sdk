# ADR: EmptyDir Session Checkpoint Storage

## Status

Accepted. Docker Compose session storage has moved to tmpfs, Astro restores checkpoints before Pi
launch, and the checkpoint sidecar flushes complete bundles to the backend. Recovery tests are still
pending.

## Context

Astro currently treats the PVC-like runtime root as the canonical durable store for Pi runtime
sessions and Astro stream artifacts. That worked for one runtime instance, but it does not scale
cleanly when agents are spawned and multiplied because multiple replicas cannot safely share one
writable Pi/Astro session filesystem.

This ADR depends on backend-owned `AgentSession` allocation. The backend should own the session id,
initial metadata, initial checkpoint row, and optional initial lease before Astro restores local
files and launches Pi.

Pi also has an important runtime restriction: session continuity is file-backed. Astro launches Pi
with:

```text
pi --mode json --session <session>.jsonl
```

Pi then reads and appends its own JSONL session file. That file is the canonical resumable context.
It contains user/assistant/tool messages, model changes, branch structure, and compaction entries.
The Astro history snapshot is useful for UI hydration, but it is not sufficient to restore Pi.
Session history for UI hydration should be reconstructed by Astro and remain separate from the Pi
checkpoint bundle.

We do not want to put Postgres on the hot path for every streamed chat chunk. We want Pi to keep
using local files while making pod-local storage disposable and horizontally scalable.

## Decision

Astro will move deployed session runtime state from a shared PVC model to a pod-local `emptyDir`
plus backend checkpoint model.

In the current long-lived `astro-pi-stream` shape, restore is handled by Astro per request, not by
an init container. Init containers run once per pod, while `astro-pi-stream` may serve many sessions.

The target ownership split is:

- Astro owns session restore, lease acquisition, and Pi child-process launch.
- Pi owns local JSONL mutation.
- A sidecar owns continuous durable checkpoint flushing from `emptyDir` to the backend.
- The backend/Postgres owns authoritative session checkpoints and leases.

The sidecar should be intentionally simple. It watches local files only for sessions that already
have a local checkpoint manifest created by Astro after backend lease/restore. Bare files in
`emptyDir` do not define a session and must not be discovered as sessions.

## Runtime Flow

### Restore and execution

1. The frontend or backend control plane sends `POST /api/chat` with an existing
   `runtime_session_id`.
2. Astro attaches to that existing backend `AgentSession.id`; this remains the frontend-visible
   runtime session id.
3. Astro acquires or renews a backend lease for that session.
4. Astro checks a local manifest in the mounted `emptyDir`.
5. If the local manifest is missing or older than the backend checkpoint version, Astro restores the
   session bundle from the backend into `emptyDir`.
6. Astro launches Pi using the restored local file:

   ```text
   pi --mode json --session /session-state/sessions/<session_id>.jsonl
   ```

7. Astro continues writing local stream artifacts as it does today.
8. The sidecar observes local changes for manifest-backed sessions and flushes durable checkpoints.

### Flush and checkpoint

The sidecar discovers flushable sessions from local manifest files and checkpoint markers, not from
raw session files. It watches session files only after a manifest exists, then flushes the complete
session bundle back to the backend.

Flush triggers:

- local file hash or mtime changes after a short debounce window
- explicit checkpoint marker written by Astro after `finish`
- explicit checkpoint marker written by Astro after `error`
- detection of a new Pi `compaction` JSONL entry
- periodic dirty safety interval
- `SIGTERM`, Kubernetes `preStop`, or process shutdown

The sidecar maps flush triggers to backend request reasons as follows:

```python
checkpoint_flush_reasons = {
    "stream_finish",
    "stream_error",
    "periodic",
    "compaction",
    "shutdown",
}
```

Shutdown flushes are represented as normal flush requests with `reason = "shutdown"`. They do not
use a separate request flag.

The sidecar must flush complete files only:

- JSON files must parse successfully.
- JSONL files must end on a complete line.
- malformed or partial trailing lines must not be uploaded.
- each uploaded checkpoint includes content hashes so the backend can detect no-op writes.

Astro may optionally call a local sidecar HTTP endpoint such as:

```http
POST http://127.0.0.1:<sidecar-port>/flush
Content-Type: application/json

{ "session_id": "123", "reason": "finish" }
```

That explicit signal gives low-latency durability at turn boundaries while preserving the sidecar
as the component responsible for checkpoint upload mechanics.

## Session Bundle

The durable checkpoint bundle must include every file required for Pi/runtime restore:

```text
<session_id>.jsonl
<session_id>.meta.json
<thread_id>.thread.json
```

Optional bundle members:

```text
/session-state/session-overrides/<session_id>/settings.json
```

The Pi JSONL file is mandatory for real session continuation. The history snapshot is not a
replacement for Pi state because it excludes reasoning, tool details, branch metadata, model
changes, and compaction entries.

### Astro-owned session history

Astro UI/session history must move out of the emptyDir checkpoint bundle as an authoritative
storage concern. The frontend-facing history shape is owned by Astro, not by the backend.

These files are not authoritative checkpoint files:

```text
<session_id>.conversation.jsonl
<session_id>.history.json
```

During migration, Astro may still write them as local runtime cache files, but the sidecar must not
treat them as required checkpoint bundle members. They are disposable with the pod.

This ADR originally assumed Astro would own projection of backend session/checkpoint data into the
frontend chat-history response. The active system no longer exposes that Astro-owned history
endpoint, but the projected response shape described below remains the same:

```python
conversation_history_snapshot = {
    "version": 1,
    "session": {
        "sessionId": str,
        "threadId": str,
        "agentName": str,
        "agentId": int | None,
        "agentSessionId": int | None,
        "status": "running" | "completed" | "error",
        "startedAt": str | None,
        "updatedAt": str | None,
        "error": str | None,
    },
    "messages": [
        {
            "id": str,
            "role": "user" | "assistant",
            "createdAt": str,
            "completedAt": str | None,
            "content": [
                {
                    "type": "text",
                    "text": str,
                },
            ],
        },
    ],
    "inProgressMessage": {
        "id": str,
        "role": "user" | "assistant",
        "createdAt": str,
        "completedAt": str | None,
        "content": [
            {
                "type": "text" | "reasoning",
                "text": str,
            },
        ],
    } | None,
}
```

Astro reconstruction order:

- If the active pod has local `<session_id>.history.json`, Astro returns that full snapshot.
- If local history is missing, Astro fetches the backend `AgentSession` for the session envelope.
- Astro then fetches the backend latest checkpoint and rebuilds `messages` from
  `bundle.pi_session_jsonl`.
- Astro walks the current Pi branch, includes user messages, assistant text, and assistant
  reasoning as structured `reasoning` parts, excludes tool traffic, writes the rebuilt snapshot back
  to local `<session_id>.history.json`, and returns that snapshot to the frontend.
- Pi native `thinking` blocks and literal provider `<think>...</think>` text are normalized into
  structured `reasoning` parts. Astro must not return raw thinking tags as assistant text.
- User messages stored in Pi as Astro prompt wrappers are projected back to the frontend-visible
  latest user message only; internal `UI context`, `UI tools`, bootstrap text, and prompt keys must
  not leak into chat history.
- The backend does not store Astro's frontend snapshot shape. It stores the generic checkpoint
  bundle; Astro owns the projection into the frontend history response.

Errors returned by Astro:

```python
astro_chat_history_error_response = {
    "error": "session_not_found" | "history_not_available",
    "message": str,
}
```

The intended read paths are:

- Pi resume reads the backend checkpoint bundle and restores `<session_id>.jsonl`.
- Frontend chat hydration reads a projected history snapshot built from durable session state.

Local cache writes should happen at message or turn boundaries, not per streamed token/chunk.

## Local EmptyDir Layout

In deployed pods, mount a writable `emptyDir` for session runtime files:

```text
/session-state/
├── sessions/
│   ├── 123.jsonl
│   ├── 123.meta.json
│   └── thread-abc.thread.json
├── manifests/
│   └── 123.manifest.json
└── checkpoints/
    └── 123.finish.marker.json
```

Astro should set:

```text
ASTRO_STREAM_SESSION_DIR=/session-state/sessions
```

The manifest records the backend checkpoint that local files represent:

```json
{
  "session_id": "123",
  "checkpoint_version": 17,
  "restored_at": "2026-04-21T10:00:00.000Z",
  "bundle_hash": "sha256:...",
  "lease_holder_id": "pod/astro-pi-stream-abc",
  "lease_token": "0b6dd96c-e013-49ad-b5df-62670f6d5e42",
  "lease_expires_at": "2026-04-21T10:02:00.000Z"
}
```

## Docker Compose Impact

Local Docker Compose should stop emulating a deployment PVC for session state once this ADR is
implemented.

The old shared local-volume model is intentionally removed from the scaling path, so Compose should
use container-local ephemeral paths instead:

- set `ASTRO_STREAM_SESSION_DIR=/session-state/sessions`
- mount `/session-state` from a tmpfs-backed Compose volume shared by `astro-pi-stream` and the
  checkpoint sidecar, because service-level Compose `tmpfs` mounts are private per container

Any non-session state that still appears to require the old volume should be revisited during
implementation. Production should prefer runtime credentials, backend-owned checkpoints, and
rebuildable local state instead of a shared durable container filesystem.

## Backend Model Shapes

The backend should store the latest checkpoint as one full session-bundle row per runtime session.
This is the chosen first implementation.

Compaction-specific retention and backend normalization are defined in
[`adr-compaction-checkpoint-retention.md`](./adr-compaction-checkpoint-retention.md). That ADR
extends the `checkpoint/flush/` response so the backend can return a pruned bundle after accepted
compaction flushes.

Do not split the initial checkpoint store into one row per file. The sidecar restores and flushes a
complete bundle, and keeping that bundle together makes versioning, lease validation, and conflict
handling simpler. Entry-level or per-file normalization can be reconsidered later only if query
performance, audit history, or diffing needs justify the extra write-path complexity.

### `agent_session_checkpoints`

Stores the authoritative latest full checkpoint for a runtime session.

```sql
CREATE TABLE agent_session_checkpoints (
  id BIGSERIAL PRIMARY KEY,
  agent_session_id BIGINT NOT NULL UNIQUE,
  checkpoint_version BIGINT NOT NULL DEFAULT 0,
  bundle_hash TEXT NOT NULL DEFAULT '',
  pi_session_jsonl TEXT NOT NULL DEFAULT '',
  astro_metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  thread_binding_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  session_overrides_json JSONB,
  last_flushed_by TEXT,
  last_flush_reason TEXT,
  last_flush_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Notes:

- `agent_session_id` should reference the existing backend `AgentSession.id`.
- `checkpoint_version` is owned by the backend. It is incremented only when a flush stores a new
  checkpoint bundle. No-op flushes return the current version without incrementing.
- `bundle_hash` is a hash of the normalized bundle contents.
- `pi_session_jsonl` is the canonical Pi restore source.
- `thread_binding_json` is required for new accepted flushes so restore can recreate the local
  thread/session binding file.
- UI/session history is intentionally not stored in this checkpoint row. Astro owns the
  frontend-facing history projection and reconstructs it from local cache or generic backend
  session data.

### `agent_session_leases`

Prevents two pods from mutating the same session concurrently.

```sql
CREATE TABLE agent_session_leases (
  agent_session_id BIGINT PRIMARY KEY,
  holder_id TEXT NOT NULL,
  lease_token UUID NOT NULL,
  acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  checkpoint_version_at_acquire BIGINT NOT NULL DEFAULT 0
);
```

Lease rules:

- acquire succeeds when no row exists or `expires_at < now()`
- renew succeeds only for matching `lease_token`
- flush should include the active `lease_token`
- backend should reject flushes from non-holder or expired leases unless explicitly configured for
  emergency recovery

### `agent_session_checkpoint_events`

Append-only audit log for restore and flush operations.

```sql
CREATE TABLE agent_session_checkpoint_events (
  id BIGSERIAL PRIMARY KEY,
  agent_session_id BIGINT NOT NULL,
  checkpoint_version BIGINT,
  event_type TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  reason TEXT,
  bundle_hash TEXT,
  success BOOLEAN NOT NULL,
  error_detail TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Suggested `event_type` values:

```text
lease_acquired
lease_renewed
lease_released
restore_started
restore_completed
flush_started
flush_completed
flush_rejected
shutdown_flush_completed
```

## Backend API Contract

Astro and the sidecar use these exact backend operations for checkpoint storage. The paths use the
existing `AgentSession` resource because the checkpoint is keyed by the backend `AgentSession.id`.

The backend owns all `checkpoint_version` changes. Astro and the sidecar only send the version they
last restored or flushed successfully.

The backend must own the authoritative checkpoint logic, not just persistence. Astro and the
sidecar may pre-validate local files before calling the backend, but the backend is the final
authority for:

- whether a lease can be acquired, renewed, released, or used
- whether a restore is allowed for the current holder
- whether a flush is accepted, rejected, or treated as a no-op
- whether the submitted checkpoint version is current
- whether the submitted bundle hash matches the normalized bundle contents
- when `checkpoint_version` increments
- which audit event is recorded for each accepted or rejected operation

Astro owns runtime workflow decisions, local file materialization, and manifest creation. The
sidecar owns watching manifest-backed local files and submitting complete checkpoint bundles.
Neither Astro nor the sidecar may locally advance `checkpoint_version` or bypass backend
lease/version decisions.

### Lease identity fields

The lease fields have different jobs and must not be collapsed into one value.

```python
checkpoint_lease_identity = {
    "agent_session_id": int,
    "holder_id": str,
    "lease_token": str,
}
```

Field rules:

- `agent_session_id` is the existing backend `AgentSession.id`. It identifies the session resource
  being protected.
- `holder_id` is the runtime writer identity that is trying to hold the lease for that session. It
  is not an Agent id, not an AgentSession id, not a thread id, and not a user id.
- `lease_token` is an opaque backend-generated UUID returned by lease acquire. It proves that the
  caller is using the active lease.

The `AgentSession` foreign key answers: "which session is being protected?"

`holder_id` answers: "which runtime pod/process currently owns the right to mutate this session's
local files and flush its checkpoint?"

The backend needs both because two Astro replicas can ask to run the same `AgentSession.id`. The
foreign key alone identifies the row; it does not identify the current writer. The backend stores
`holder_id` on `agent_session_leases` so it can reject a different runtime trying to renew, restore,
flush, or release another runtime's lease.

`holder_id` must be stable for the lifetime of the runtime holder and shared by the Astro container
and checkpoint sidecar that operate on the same mounted `emptyDir`. In Kubernetes it should come
from the pod identity, preferably the pod UID passed through environment. In local Docker Compose it
should be a deterministic compose runtime id shared by `astro-pi-stream` and the checkpoint sidecar.

`holder_id` is not sufficient authorization by itself. Mutating endpoints must require both the
matching `holder_id` and the matching backend-generated `lease_token`.

### Checkpoint bundle shape

All restore and flush operations use one full bundle object, not one row per file.

```python
checkpoint_bundle = {
    "pi_session_jsonl": str,
    "astro_metadata_json": dict,
    "thread_binding_json": dict,
    "session_overrides_json": dict | None,
}
```

### Backend validation contract

The backend must enforce this validation on checkpoint writes. Astro and the sidecar may perform
the same checks locally to avoid bad requests, but backend validation is authoritative.

Mandatory checkpoint fields for a new accepted flush:

```python
mandatory_checkpoint_fields = {
    "pi_session_jsonl": str,
    "astro_metadata_json": dict,
    "thread_binding_json": dict,
}
```

Optional checkpoint fields:

```python
optional_checkpoint_fields = {
    "session_overrides_json": dict | None,
}
```

Validation rules:

- `pi_session_jsonl` must be present as a string. Empty string is allowed only for an initialized
  checkpoint before Pi has written the first session event.
- Non-empty `pi_session_jsonl` must be complete JSONL: it must end with `\n`, every non-empty line
  must parse as JSON, and the backend must reject a partial trailing line.
- `astro_metadata_json` must be present and must be a JSON object.
- `thread_binding_json` must be present and must be a JSON object.
- `session_overrides_json` may be null. When present, it must be a JSON object.
- JSON object fields must not be strings containing JSON; the backend should receive and store real
  JSON objects from the parsed request body.
- `bundle_hash` must be present on flush and must equal the backend recomputation of the normalized
  bundle.
- The normalized bundle hash must include the exact `pi_session_jsonl` string plus canonical JSON
  serialization of `astro_metadata_json`, `thread_binding_json`, and `session_overrides_json`.
- Canonical JSON serialization means recursive lexical key ordering by Unicode/code-point value and
  no runtime-dependent whitespace. Implementations must not use locale-aware sorting.
- The exact hash input is canonical JSON for this object, with `session_overrides_json` normalized to
  null when no override settings exist:

  ```python
  normalized_checkpoint_bundle_for_hash = {
      "astro_metadata_json": astro_metadata_json,
      "pi_session_jsonl": pi_session_jsonl,
      "session_overrides_json": session_overrides_json_or_none,
      "thread_binding_json": thread_binding_json,
  }

  bundle_hash = "sha256:" + sha256(canonical_json(normalized_checkpoint_bundle_for_hash)).hexdigest()
  ```

- `expected_checkpoint_version` must be present on flush and must equal the locked backend
  `agent_session_checkpoints.checkpoint_version`.
- `lease_token` and `holder_id` must match the active lease row for renew, restore, flush, and
  release.
- Renew, restore, and flush require the active lease to be unexpired. Release may delete an expired
  lease only when both `holder_id` and `lease_token` still match.

Validation error codes:

```python
checkpoint_validation_errors = {
    "checkpoint_missing_mandatory_field",
    "checkpoint_invalid_field_type",
    "checkpoint_invalid_jsonl",
    "checkpoint_not_found",
    "checkpoint_bundle_hash_mismatch",
    "checkpoint_lease_missing",
    "checkpoint_lease_expired",
    "checkpoint_lease_token_mismatch",
    "checkpoint_lease_holder_mismatch",
    "checkpoint_lease_already_held",
    "checkpoint_version_conflict",
}
```

### Acquire checkpoint lease

```http
POST /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint_lease/acquire/
```

Exact request body:

```python
checkpoint_lease_acquire_request = {
    "holder_id": holder_id,
    "ttl_seconds": ttl_seconds,
}
```

Request field rules:

- `holder_id` is required and must be a non-empty string.
- `ttl_seconds` is required and must be a positive integer.

Exact success response body:

```python
checkpoint_lease_acquire_response = {
    "agent_session_id": agent_session_id,
    "holder_id": holder_id,
    "lease_token": lease_token,
    "lease_expires_at": lease_expires_at,
    "checkpoint_version": checkpoint_version,
    "bundle_hash": bundle_hash,
}
```

Backend logic:

1. Validate that `AgentSession(id=agent_session_id)` exists.
2. Validate `holder_id` and `ttl_seconds`.
3. Open a database transaction.
4. Lock or create the `agent_session_checkpoints` row for `agent_session_id`.
5. Lock the `agent_session_leases` row for `agent_session_id` if it exists.
6. If there is no lease row, or the lease is expired, write a new lease with a new
   `lease_token`, `holder_id`, `heartbeat_at = now()`, `expires_at = now() + ttl_seconds`, and
   `checkpoint_version_at_acquire = checkpoint.checkpoint_version`.
7. If an unexpired lease exists, reject with `409 checkpoint_lease_already_held`.
8. Append an `agent_session_checkpoint_events` row with `event_type = "lease_acquired"` and
   `actor_id = holder_id`.
9. Return the lease token plus the current checkpoint version and bundle hash.

### Renew checkpoint lease

```http
POST /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint_lease/renew/
```

Exact request body:

```python
checkpoint_lease_renew_request = {
    "holder_id": holder_id,
    "lease_token": lease_token,
    "ttl_seconds": ttl_seconds,
}
```

Request field rules:

- `holder_id` is required and must match the current lease row.
- `lease_token` is required and must match the current lease row.
- `ttl_seconds` is required and must be a positive integer.

Exact success response body:

```python
checkpoint_lease_renew_response = {
    "agent_session_id": agent_session_id,
    "holder_id": holder_id,
    "lease_token": lease_token,
    "lease_expires_at": lease_expires_at,
    "checkpoint_version": checkpoint_version,
    "bundle_hash": bundle_hash,
}
```

Backend logic:

1. Validate that `AgentSession(id=agent_session_id)` exists.
2. Validate `holder_id`, `lease_token`, and `ttl_seconds`.
3. Open a database transaction.
4. Lock the `agent_session_leases` row.
5. Reject with `409 checkpoint_lease_missing` if no lease exists.
6. Reject with `409 checkpoint_lease_expired` if `expires_at <= now()`.
7. Reject with `409 checkpoint_lease_token_mismatch` if `lease_token` does not match.
8. Reject with `409 checkpoint_lease_holder_mismatch` if `holder_id` does not match.
9. Set `heartbeat_at = now()` and `expires_at = now() + ttl_seconds`.
10. Append an `agent_session_checkpoint_events` row with `event_type = "lease_renewed"` and
    `actor_id = holder_id`.
11. Return the renewed expiry plus the current checkpoint version and bundle hash.

### Release checkpoint lease

```http
POST /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint_lease/release/
```

Exact request body:

```python
checkpoint_lease_release_request = {
    "holder_id": holder_id,
    "lease_token": lease_token,
    "reason": reason,
}
```

Request field rules:

- `holder_id` is required and must match the current lease row.
- `lease_token` is required and must match the current lease row.
- `reason` is required and records why the holder is releasing the lease.

Exact success response body:

```python
checkpoint_lease_release_response = {
    "agent_session_id": agent_session_id,
    "released": True,
    "checkpoint_version": checkpoint_version,
    "bundle_hash": bundle_hash,
}
```

Backend logic:

1. Validate that `AgentSession(id=agent_session_id)` exists.
2. Validate `holder_id`, `lease_token`, and `reason`.
3. Open a database transaction.
4. Lock the `agent_session_leases` row.
5. Reject with `409 checkpoint_lease_missing` if no lease exists.
6. Reject with `409 checkpoint_lease_token_mismatch` if `lease_token` does not match.
7. Reject with `409 checkpoint_lease_holder_mismatch` if `holder_id` does not match.
8. Delete the lease row.
9. Append an `agent_session_checkpoint_events` row with `event_type = "lease_released"`,
   `actor_id = holder_id`, and `reason = reason`.
10. Return the current checkpoint version and bundle hash.

### Read latest checkpoint bundle

```http
GET /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint/latest/
```

Exact request body: none.

Exact success response body:

```python
checkpoint_latest_response = {
    "agent_session_id": agent_session_id,
    "checkpoint_version": checkpoint_version,
    "bundle_hash": bundle_hash,
    "updated_at": updated_at,
    "bundle": checkpoint_bundle,
}
```

Backend logic:

1. Validate that `AgentSession(id=agent_session_id)` exists.
2. Read the latest checkpoint row for that `AgentSession`.
3. Reject with `404 checkpoint_not_found` if no checkpoint row exists.
4. Return the complete latest checkpoint bundle.

This endpoint is read-only. It must not acquire, renew, validate, or release leases. Astro uses it
only for frontend history hydration after local emptyDir history has disappeared.

Until this endpoint is deployed, Astro may use a compatibility fallback for history hydration:

1. Acquire a short checkpoint lease with a holder id ending in `/history`.
2. Call `POST /checkpoint/restore/`.
3. Release the lease with `reason = "history_hydration"`.
4. Project the returned `bundle.pi_session_jsonl` into frontend history.

This fallback is slower and writes lease/audit state. It should be removed once
`checkpoint/latest/` exists in the backend.

### Restore checkpoint bundle

```http
POST /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint/restore/
```

Exact request body:

```python
checkpoint_restore_request = {
    "holder_id": holder_id,
    "lease_token": lease_token,
}
```

Request field rules:

- `holder_id` is required and must match the current lease row.
- `lease_token` is required and must match the current lease row.

Exact success response body:

```python
checkpoint_restore_response = {
    "agent_session_id": agent_session_id,
    "checkpoint_version": checkpoint_version,
    "bundle_hash": bundle_hash,
    "updated_at": updated_at,
    "bundle": checkpoint_bundle,
}
```

Backend logic:

1. Validate that `AgentSession(id=agent_session_id)` exists.
2. Validate `holder_id` and `lease_token`.
3. Open a database transaction.
4. Lock the `agent_session_checkpoints` row.
5. Lock the `agent_session_leases` row.
6. Reject with `409 checkpoint_lease_missing` if no lease exists.
7. Reject with `409 checkpoint_lease_expired` if `expires_at <= now()`.
8. Reject with `409 checkpoint_lease_token_mismatch` if `lease_token` does not match.
9. Reject with `409 checkpoint_lease_holder_mismatch` if `holder_id` does not match.
10. Append `restore_started` and `restore_completed` audit events with `actor_id = holder_id`.
11. Return the complete latest checkpoint bundle.

### Flush checkpoint bundle

```http
POST /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint/flush/
```

Exact request body:

```python
checkpoint_flush_request = {
    "holder_id": holder_id,
    "lease_token": lease_token,
    "expected_checkpoint_version": expected_checkpoint_version,
    "reason": reason,
    "bundle_hash": bundle_hash,
    "bundle": checkpoint_bundle,
}
```

Request field rules:

- `holder_id` is required and must match the current lease row.
- `lease_token` is required and must match the current lease row.
- `expected_checkpoint_version` is required and must equal the backend checkpoint row's current
  `checkpoint_version`.
- `reason` is required and must be one of `stream_finish`, `stream_error`, `periodic`,
  `compaction`, or `shutdown`.
- `bundle_hash` is required and must equal the backend recomputation of the normalized bundle.
- `bundle.pi_session_jsonl` is required.
- `bundle.astro_metadata_json` is required.
- `bundle.thread_binding_json` is required.

Exact success response body:

```python
checkpoint_flush_response = {
    "agent_session_id": agent_session_id,
    "checkpoint_version": checkpoint_version,
    "bundle_hash": bundle_hash,
    "noop": noop,
    "updated_at": updated_at,
}
```

Backend logic:

1. Validate that `AgentSession(id=agent_session_id)` exists.
2. Validate `holder_id`, `lease_token`, `expected_checkpoint_version`, `reason`, `bundle_hash`,
   and `bundle`.
3. Validate that `pi_session_jsonl`, `astro_metadata_json`, and `thread_binding_json` are present.
4. Validate that JSON fields are objects when not null.
5. Validate that `pi_session_jsonl` is complete JSONL when non-empty.
6. Recompute the normalized bundle hash and reject with `400 checkpoint_bundle_hash_mismatch` if it
   does not match the submitted `bundle_hash`.
7. Open a database transaction.
8. Lock the `agent_session_checkpoints` row.
9. Lock the `agent_session_leases` row.
10. If no lease exists, append `flush_rejected` and reject with `409 checkpoint_lease_missing`.
11. If `expires_at <= now()`, append `flush_rejected` and reject with
    `409 checkpoint_lease_expired`.
12. If `lease_token` does not match, append `flush_rejected` and reject with
    `409 checkpoint_lease_token_mismatch`.
13. If `holder_id` does not match, append `flush_rejected` and reject with
    `409 checkpoint_lease_holder_mismatch`.
14. If `expected_checkpoint_version` does not equal the current backend `checkpoint_version`,
    append `flush_rejected` and reject with `409 checkpoint_version_conflict`.
15. If the recomputed bundle hash equals the current backend `bundle_hash`, return the current
    `checkpoint_version` with `noop = True`.
16. If the bundle changed, update the full checkpoint row, set
    `checkpoint_version = checkpoint_version + 1`, set `last_flushed_by = holder_id`, set
    `last_flush_reason = reason`, set `last_flush_at = now()`, and append a
    `flush_completed` audit event with `actor_id = holder_id`.
17. Return the new backend-owned checkpoint version with `noop = False`.

### Error response shape

All checkpoint endpoints should return this shape on rejected operations:

```python
checkpoint_error_response = {
    "agent_session_id": agent_session_id,
    "error_code": error_code,
    "error_detail": error_detail,
    "checkpoint_version": checkpoint_version,
    "bundle_hash": bundle_hash,
}
```

When the backend returns `checkpoint_version_conflict`, Astro or the sidecar must stop writing that
local manifest and restore the backend checkpoint before launching or flushing again.

## Concurrency Rules

- Only one active writer may hold a lease for a session.
- Astro must acquire a lease before restoring or launching Pi for a session.
- Astro should renew the lease while a Pi child process is active.
- The sidecar must include the lease token with every flush.
- A new pod may take over only after lease expiry or an explicit forced administrative handoff.
- If local files are older than backend checkpoint version, Astro must restore before launching Pi.
- If local files are newer but the lease is lost, the sidecar may attempt one final shutdown flush;
  backend decides whether to accept or reject it.

## Consequences

### Positive

- Pi keeps its local file contract.
- Postgres is not hit for every streamed chunk.
- Shared writable PVC state is removed from the scaling path.
- Session restore works on any replica that can acquire the lease.
- Sidecar persistence can evolve independently from Pi and Astro stream code.

### Negative

- Sidecar correctness becomes critical for durability.
- A crash can lose data written after the last successful checkpoint.
- Lease and checkpoint version handling adds backend complexity.
- Restore is now a per-request concern inside Astro for long-lived stream pods.
- Large JSONL checkpoints may become expensive without later incremental uploads.

## Non-Goals

- Rewriting Pi to use Postgres directly.
- Using the checkpoint row as the frontend/session-history store.
- Supporting concurrent writes to the same session.

## Implementation Tasks

- [x] Add the backend full-bundle checkpoint storage table or equivalent ORM model.
- [x] Add the exact backend checkpoint endpoints from this ADR: lease acquire, lease renew,
      lease release, checkpoint restore, and checkpoint flush.
- [x] Implement the backend validation contract defined in this ADR: mandatory checkpoint fields,
      JSON object types, complete JSONL, normalized bundle hash, lease tokens, and checkpoint
      versions.
- [x] Keep Astro UI/session history out of backend checkpoint storage and have Astro own the
      frontend-facing history projection keyed by `AgentSession.id`; `thread_id` is optional
      frontend correlation only.
- [ ] Add the backend read-only latest checkpoint endpoint used by Astro history hydration:
      `GET /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint/latest/`.
- [x] Update Astro's historical history-hydration path to return local `.history.json` when
      present, otherwise fetch backend `AgentSession` plus `checkpoint/latest/` and rebuild the
      full frontend transcript from `bundle.pi_session_jsonl`.
- [x] Update read endpoints that depend on local session metadata or Pi JSONL
      (`session-model`, `session-config`, and `session-insights`, along with the read-only
      surfaces that existed during the migration) to hydrate from backend checkpoint state before
      returning `session_not_found`.
- [x] Add an Astro `SessionCheckpointClient` for backend latest/restore/flush/lease calls.
- [x] Change deployed `ASTRO_STREAM_SESSION_DIR` to an `emptyDir` path such as
      `/session-state/sessions`.
- [x] Remove Docker Compose durable local storage for runtime/session continuity.
- [x] Update Docker Compose to use ephemeral local session state, preferably `/session-state` with
      a `tmpfs` mount when practical.
- [x] Add a Docker Compose checkpoint sidecar simulation that shares the same tmpfs-backed
      `/session-state` volume as `astro-pi-stream`.
- [x] Stop bootstrap from reading repo-local stream-session files into active emptyDir session
      storage.
- [x] Add a local sidecar runner that watches manifest-backed session files/checkpoint markers and
      logs debounced checkpoint candidates.
- [x] Add Astro restore-before-Pi-launch logic keyed by `runtime_session_id`.
- [x] Add a local manifest file that records restored checkpoint version, bundle hash, and lease
      holder.
- [x] Add lease acquisition/renewal before launching a Pi child process.
- [x] Add explicit Astro checkpoint markers or local sidecar HTTP calls after stream `finish` and
      `error`.
- [x] Build the production checkpoint sidecar process that flushes bundles to the backend.
- [x] Sidecar watches Pi JSONL, Astro metadata, thread binding, and session override files only for
      sessions with a local checkpoint manifest.
- [x] Sidecar debounces dirty files and performs periodic safety flushes.
- [x] Sidecar detects Pi compaction entries and forces a checkpoint.
- [x] Sidecar performs best-effort final flush on `SIGTERM` and Kubernetes `preStop`.
- [x] Add pod spec changes: shared `emptyDir`, Astro container mount, sidecar mount, sidecar env.
- [x] Add observability: restore count, flush count, flush latency, rejected flushes, lease failures,
      dirty age, last checkpoint version.
- [ ] Add recovery tests for pod crash, stale local manifest, expired lease, invalid partial JSONL,
      compaction checkpoint, and same-session concurrent request rejection.
- [x] Update `docs/reference/persistent-state.md` after implementation to document the stateless
      runtime storage contract.
