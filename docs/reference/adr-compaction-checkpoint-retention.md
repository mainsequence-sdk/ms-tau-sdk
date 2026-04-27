# ADR: Compaction Checkpoint Retention

## Status

Proposed.

## Context

Pi compaction reduces the model context, but Pi's session file is append-only. A compaction entry
does not automatically delete older JSONL entries from the file.

The compaction entry shape is:

```python
pi_compaction_entry = {
    "type": "compaction",
    "id": str,
    "parentId": str | None,
    "timestamp": str,
    "summary": str,
    "firstKeptEntryId": str,
    "tokensBefore": int,
    "details": dict | None,
    "fromHook": bool | None,
}
```

Pi restores context from this entry by:

- injecting the compaction summary into the model context
- keeping messages from `firstKeptEntryId` through the compaction boundary
- keeping messages after the compaction boundary

This means a backend that stores the raw latest `pi_session_jsonl` without pruning can still retain
the full pre-compaction conversation forever. That is not acceptable now that backend storage is the
durable owner of session continuity.

## Decision

Compaction is a durable retention boundary.

When the backend accepts a checkpoint flush whose active branch contains a new compaction entry, the
backend must normalize the checkpoint before storing it. Normalization means pruning the
pre-compaction JSONL entries that are no longer required for Pi restore.

The backend must store only the latest checkpoint bundle per `AgentSession.id`. It must not keep old
checkpoint bundle payloads, old `pi_session_jsonl` values, or frontend history snapshots for normal
session continuity.

The backend may keep existing checkpoint events for audit and debugging. Event rows must use the
existing event columns from the emptyDir checkpoint ADR. They must not store checkpoint bundle
payloads. Compaction details live on the latest checkpoint row fields added by this ADR.

## Ownership

Backend owns:

- the authoritative latest checkpoint bundle
- lease and checkpoint version decisions
- bundle hash validation
- compaction detection on accepted flushes
- compaction pruning and normalized bundle hashing
- retention fields on the checkpoint row
- existing checkpoint event records without bundle payloads

Astro owns:

- launching Pi against local files
- restoring backend checkpoint bundles into `emptyDir`
- projecting frontend chat history from the latest backend checkpoint
- deleting local frontend history cache after backend compaction normalization
- rendering a compacted-history boundary instead of deleted pre-compaction messages

Checkpoint sidecar owns:

- detecting local Pi compaction entries
- flushing complete checkpoint bundles to the backend
- accepting normalized bundles returned by the backend
- rewriting local `emptyDir` files to match the backend-normalized bundle
- deleting local `.history.json` and `.conversation.jsonl` after compaction normalization

## Additive Backend Persistence

The base `AgentSessionCheckpoint`, checkpoint lease, and checkpoint event persistence already exist
from [`adr-emptydir-session-checkpoint-storage.md`](./adr-emptydir-session-checkpoint-storage.md).
This ADR must not redefine those models.

### Database Migration

Add exactly these Django fields to the existing `AgentSessionCheckpoint` model:

```python
latest_compaction_entry_id = models.CharField(max_length=255, blank=True, default="")
latest_compaction_at = models.DateTimeField(null=True, blank=True)
latest_compaction_first_kept_entry_id = models.CharField(max_length=255, blank=True, default="")
latest_compaction_tokens_before = models.PositiveBigIntegerField(null=True, blank=True)
compaction_retention_applied_at = models.DateTimeField(null=True, blank=True)
compaction_pruned_entry_count = models.PositiveIntegerField(default=0)
```

No other checkpoint model fields are added by this ADR.

### Existing Event Rows

Do not add fields to the checkpoint event model for compaction.

There is no separate compaction event producer. The existing
`POST /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint/flush/` handler writes the existing
`agent_session_checkpoint_events` audit row.

The backend knows a flush is compaction-related in exactly these cases:

- the request body has `reason = "compaction"`
- or the backend parses `bundle.pi_session_jsonl`, finds a new active-branch `type = "compaction"`
  entry, and applies retention normalization

For an accepted compaction-normalized flush, write the existing event row with these existing
columns:

```python
checkpoint_event_on_compaction_flush = {
    "agent_session_id": agent_session_id,
    "checkpoint_version": checkpoint_version,
    "event_type": "flush_completed",
    "actor_id": holder_id,
    "reason": "compaction",
    "bundle_hash": "sha256:<stored_bundle_hash>",
    "success": True,
    "error_detail": "",
}
```

For a rejected compaction flush request, write the same existing event row shape with:

```python
checkpoint_event_on_rejected_compaction_flush = {
    "agent_session_id": agent_session_id,
    "checkpoint_version": checkpoint_version,
    "event_type": "flush_rejected",
    "actor_id": holder_id,
    "reason": "compaction",
    "bundle_hash": request_bundle_hash,
    "success": False,
    "error_detail": "<exact rejection code and backend detail>",
}
```

For a rejected compaction flush event row, `bundle_hash` is the submitted request hash because no
new checkpoint bundle was persisted.

## Backend API Contract

This ADR extends the existing checkpoint flush operation. It does not add a separate compaction
endpoint.

The request and response shapes below are HTTP API payloads for `checkpoint/flush/`. They are not
`agent_session_checkpoint_events` rows. The event row written by the flush handler is described in
the previous section and uses only the existing event columns.

### Flush Request

```http
POST /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint/flush/
```

Request body:

```python
checkpoint_flush_request = {
    "holder_id": str,
    "lease_token": str,
    "expected_checkpoint_version": int,
    "reason": "stream_finish" | "stream_error" | "periodic" | "compaction" | "shutdown",
    "bundle_hash": "sha256:<submitted_bundle_hash>",
    "bundle": {
        "pi_session_jsonl": str,
        "astro_metadata_json": dict,
        "thread_binding_json": dict,
        "session_overrides_json": dict | None,
    },
    "client_capabilities": {
        "accepts_normalized_bundle": True,
    },
}
```

Rules:

- The backend must reject `reason = "compaction"` without `accepts_normalized_bundle = True`, because
  the sidecar must be able to rewrite local files to match backend-normalized state.
- For `reason = "stream_finish"`, `reason = "stream_error"`, `reason = "periodic"`, or
  `reason = "shutdown"`, `client_capabilities` is ignored.

### Hash Contract

`request.bundle_hash = sha256:<submitted_bundle_hash>`.

The backend validates `request.bundle_hash` against the exact submitted `request.bundle` before
retention normalization.

If retention normalization applies, the backend computes `stored_bundle_hash` from the normalized
bundle that will be persisted.

`response.bundle_hash` always means the persisted checkpoint bundle hash.

For compaction normalization, `response.bundle_hash != request.bundle_hash` is valid and expected.

### Flush Response: No Change Accepted

If the final bundle that would be stored has the same hash as the current stored checkpoint:

```python
checkpoint_flush_noop_response = {
    "agent_session_id": int,
    "checkpoint_version": int,
    "bundle_hash": "sha256:<stored_bundle_hash>",
    "noop": True,
    "updated_at": str | None,
    "normalized": False,
    "normalized_reason": None,
    "bundle": None,
}
```

If the backend normalized the submitted bundle before reaching the no-op decision, it must use the
compaction-normalized response shape with `noop = True` and include the normalized `bundle`.

### Flush Response: Normal Accepted Flush

```python
checkpoint_flush_response = {
    "agent_session_id": int,
    "checkpoint_version": int,
    "bundle_hash": "sha256:<stored_bundle_hash>",
    "noop": False,
    "updated_at": str,
    "normalized": False,
    "normalized_reason": None,
    "bundle": None,
}
```

### Flush Response: Compaction-Normalized Flush

When backend prunes the checkpoint, it must return the normalized bundle:

```python
checkpoint_flush_compaction_response = {
    "agent_session_id": int,
    "checkpoint_version": int,
    "bundle_hash": "sha256:<stored_bundle_hash>",
    "noop": bool,
    "updated_at": str,
    "normalized": True,
    "normalized_reason": "compaction",
    "bundle": {
        "pi_session_jsonl": str,
        "astro_metadata_json": dict,
        "thread_binding_json": dict,
        "session_overrides_json": dict | None,
    },
    "retention": {
        "latest_compaction_entry_id": str,
        "latest_compaction_at": str,
        "latest_compaction_first_kept_entry_id": str,
        "latest_compaction_tokens_before": int,
        "pruned_entry_count": int,
    },
}
```

The returned `bundle_hash` must be the hash of the returned normalized `bundle`.

### Restore and Latest Response

The existing restore/latest responses should return whatever bundle is currently stored. After a
compaction-normalized flush, that means they return the pruned bundle:

```python
checkpoint_restore_response = {
    "agent_session_id": int,
    "checkpoint_version": int,
    "bundle_hash": "sha256:<stored_bundle_hash>",
    "updated_at": str | None,
    "bundle": {
        "pi_session_jsonl": str,
        "astro_metadata_json": dict,
        "thread_binding_json": dict,
        "session_overrides_json": dict | None,
    },
    "retention": {
        "latest_compaction_entry_id": str | None,
        "latest_compaction_at": str | None,
        "latest_compaction_first_kept_entry_id": str | None,
        "latest_compaction_tokens_before": int | None,
        "compaction_retention_applied_at": str | None,
        "compaction_pruned_entry_count": int,
    },
}
```

## Backend Logic

### Flush Validation

For every flush, backend must:

1. Authenticate the runtime caller.
2. Load `AgentSession` by `{agent_session_id}`.
3. Validate the active lease using `holder_id` and `lease_token`.
4. Reject stale `expected_checkpoint_version`.
5. Validate mandatory bundle members:
   - `pi_session_jsonl`
   - `astro_metadata_json`
   - `thread_binding_json`
6. Validate `pi_session_jsonl`:
   - complete JSONL lines only
   - every nonblank line parses as JSON object
   - one valid Pi session header, if present
   - entry ids are strings for session entries
7. Validate `astro_metadata_json`, `thread_binding_json`, and `session_overrides_json` are JSON
   objects or `None` where allowed.
8. Recompute the submitted bundle hash from the exact `request.bundle` payload.
9. Reject with `checkpoint_bundle_hash_mismatch` if `request.bundle_hash` does not match the exact
   submitted bundle.

### Compaction Detection

After request validation and before storing, backend must inspect the active Pi branch.

Backend must:

1. Parse `pi_session_jsonl` into ordered file entries.
2. Ignore the header entry for branch traversal.
3. Build an id map for entries with `id`.
4. Select the active leaf as the last non-header entry unless the backend later adds an explicit
   `leaf_id` field to the bundle.
5. Walk parent links from the active leaf to root.
6. Find the latest entry on that active branch where `type == "compaction"`.
7. Compare that entry id to `AgentSessionCheckpoint.latest_compaction_entry_id`.

If no active compaction exists, store the bundle normally.

If the active compaction exists and is newer than the stored checkpoint's latest compaction id,
apply retention normalization.

If the active compaction id is already stored but the submitted bundle still contains prunable
pre-compaction entries, apply retention normalization again and return the normalized bundle.

Backend must reject the flush with `invalid_compaction_entry` when:

- the compaction entry has no string `id`
- `summary` is not a string
- `firstKeptEntryId` is not a string
- `tokensBefore` is not a nonnegative integer
- `firstKeptEntryId` does not exist on the active branch before the compaction entry
- parent traversal detects a cycle
- parent traversal reaches a missing parent before it reaches the branch root

### Retention Normalization

Given the latest active compaction entry:

```python
latest_compaction = {
    "id": str,
    "timestamp": str,
    "summary": str,
    "firstKeptEntryId": str,
    "tokensBefore": int,
}
```

Backend must keep:

- the session header
- active-branch entries from `firstKeptEntryId` through the compaction entry's parent
- the compaction entry
- active-branch entries after the compaction entry
- non-message state entries on the retained active branch, including model changes, thinking level
  changes, labels, custom entries, branch summaries, and session info entries

Backend must drop:

- active-branch entries before `firstKeptEntryId`
- abandoned branches not on the active branch
- older compaction payloads not needed by the latest active compaction context
- any frontend `.conversation.jsonl` or `.history.json` content if a client accidentally submits it

The normalized JSONL must remain a valid Pi session file. Parent links for retained entries must be
valid after pruning. The output JSONL must end with a newline.

If the first retained entry has a missing parent after pruning, backend must set that retained
entry's `parentId` to `None` unless the entry is the compaction entry itself. The compaction entry
must remain after the retained recent entries so Pi can rebuild context as:

1. compaction summary
2. retained recent messages
3. post-compaction messages

The backend must not modify compaction `summary`, `tokensBefore`, or `firstKeptEntryId` values.

### Hashing and Versioning

Backend must compute hashes in this order:

1. Validate `request.bundle_hash` against the exact submitted bundle.
2. Normalize the bundle if compaction retention applies.
3. Compute `stored_bundle_hash` from the final bundle that will be stored.
4. Compare `stored_bundle_hash` to the currently stored checkpoint hash.
5. If equal, return `noop = True` without incrementing `checkpoint_version`.
6. If different, overwrite the checkpoint row and increment `checkpoint_version`.

The stored `bundle_hash` must always describe the stored bundle, not the submitted bundle.

### Persistence

On accepted compaction-normalized flush, backend must update:

```python
checkpoint_updates = {
    "checkpoint_version": checkpoint.checkpoint_version + 1,
    "bundle_hash": stored_bundle_hash,
    "pi_session_jsonl": normalized_bundle["pi_session_jsonl"],
    "astro_metadata_json": normalized_bundle["astro_metadata_json"],
    "thread_binding_json": normalized_bundle["thread_binding_json"],
    "session_overrides_json": normalized_bundle["session_overrides_json"],
    "last_flushed_by": holder_id,
    "last_flush_reason": "compaction",
    "last_flush_at": now,
    "latest_compaction_entry_id": latest_compaction["id"],
    "latest_compaction_at": latest_compaction["timestamp"],
    "latest_compaction_first_kept_entry_id": latest_compaction["firstKeptEntryId"],
    "latest_compaction_tokens_before": latest_compaction["tokensBefore"],
    "compaction_retention_applied_at": now,
    "compaction_pruned_entry_count": pruned_entry_count,
}
```

The backend must overwrite the existing checkpoint row. It must not insert a second full checkpoint
row for the same session.

### Errors

Error responses keep the existing checkpoint shape from
[`adr-emptydir-session-checkpoint-storage.md`](./adr-emptydir-session-checkpoint-storage.md):

```python
checkpoint_flush_error_response = {
    "agent_session_id": int,
    "error_code": str,
    "error_detail": str,
    "checkpoint_version": int | None,
    "bundle_hash": str,
}
```

`field_errors` may be added when useful. It is optional and additive:

```python
checkpoint_flush_error_response_with_field_errors = {
    "agent_session_id": int,
    "error_code": str,
    "error_detail": str,
    "checkpoint_version": int | None,
    "bundle_hash": str,
    "field_errors": {
        "bundle.pi_session_jsonl": ["complete JSONL lines are required"],
    },
}
```

Existing codes stay stable:

```text
checkpoint_version_conflict
checkpoint_bundle_hash_mismatch
```

Only compaction-specific codes are added:

```text
invalid_compaction_entry
compaction_normalization_failed
normalized_bundle_required
```

Meaning:

```python
compaction_error_codes = {
    "invalid_compaction_entry": (
        "the compaction entry is malformed or inconsistent with the active branch"
    ),
    "compaction_normalization_failed": (
        "backend attempted retention normalization but could not safely produce a valid "
        "normalized bundle"
    ),
    "normalized_bundle_required": (
        "backend would normalize the bundle, but the client did not advertise "
        "client_capabilities.accepts_normalized_bundle = true"
    ),
}
```

## Astro History Lifecycle

Astro's frontend history endpoint must not promise full pre-compaction transcript after backend
retention has pruned it.

After compaction normalization:

- local `<session_id>.conversation.jsonl` is deleted
- local `<session_id>.history.json` is deleted
- future `GET /api/chat/history` rebuilds from backend latest checkpoint
- the frontend history projection includes a compacted-history boundary from the compaction summary
- deleted pre-compaction user/assistant turns do not come back

The compacted-history boundary must use the existing frontend `ConversationMessage` contract. It is
inserted into the existing `conversation_history_snapshot.messages` array as a normal assistant text
message, so the frontend does not need a new message type or rendering path.

Projected message:

```python
compacted_history_message = {
    "id": "compaction_<entry_id>",
    "role": "assistant",
    "createdAt": "<compaction timestamp>",
    "completedAt": "<compaction timestamp>",
    "content": [
        {
            "type": "text",
            "text": "Previous conversation was compacted. Summary:\n\n<summary>",
        },
    ],
}
```

This is a UI projection only. The backend must not store this frontend message shape.

## Checkpoint Sidecar Lifecycle

When sidecar receives `normalized = True` and `bundle` in a successful flush response:

1. Write the returned `bundle.pi_session_jsonl` to `/session-state/sessions/<session_id>.jsonl`.
2. Write returned `astro_metadata_json` and `thread_binding_json` to local files.
3. Write or remove session overrides to match returned `session_overrides_json`.
4. Update the local manifest with returned `checkpoint_version` and `bundle_hash`.
5. Delete local `<session_id>.history.json`.
6. Delete local `<session_id>.conversation.jsonl`.
7. Clear local dirty state for that session.

After this point, local files and backend checkpoint files describe the same pruned restore state.

## Tasks

- [x] Extend backend checkpoint flush serializer to accept
      `client_capabilities.accepts_normalized_bundle`.
- [x] Reject `reason = "compaction"` when the client cannot accept a normalized bundle.
- [x] Add the compaction retention fields to the existing latest checkpoint model.
- [x] Ensure checkpoint events use the existing event columns only and never store bundle payloads.
- [x] Implement backend Pi JSONL parser and active-branch traversal.
- [x] Implement backend latest-active-compaction detection.
- [x] Implement backend compaction retention normalization.
- [x] Recompute stored bundle hash after normalization.
- [x] Return normalized bundle and retention fields in compaction flush responses.
- [x] Return retention fields from `checkpoint/restore/` and `checkpoint/latest/`.
- [x] Update sidecar `checkpoint/flush/` requests to send
      `client_capabilities.accepts_normalized_bundle = True`.
- [x] Update sidecar flush response handling to rewrite local files when `normalized = True`.
- [x] Delete local `.history.json` and `.conversation.jsonl` after normalized compaction flush.
- [x] Update Astro history projection to render compaction summary boundaries.
- [x] Add backend tests for stale version, hash mismatch, invalid JSONL, compaction pruning, and
      latest-only storage.
- [ ] Add Astro/sidecar tests for compaction flush, local rewrite, local history cache deletion,
      and restore after pod-local files are deleted.

## Consequences

Positive:

- backend storage does not retain full pre-compaction conversation after compaction
- Pi restore still works from the backend latest checkpoint
- frontend history remains useful without pretending deleted turns still exist
- checkpoint storage remains one latest row per session

Negative:

- old full transcript is intentionally unrecoverable after compaction retention is applied
- sidecar must handle backend-normalized flush responses
- history hydration needs to represent compaction boundaries explicitly
