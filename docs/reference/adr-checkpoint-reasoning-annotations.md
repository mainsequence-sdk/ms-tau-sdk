# ADR: Checkpoint Reasoning Annotations

## Status

Accepted. Astro implementation is complete in this project.

## Context

Astro live streaming can see model reasoning through Pi events such as:

```text
thinking_start
thinking_delta
thinking_end
```

Astro maps those events to frontend SSE chunks:

```text
reasoning-start
reasoning-delta
reasoning-end
```

However, the durable backend checkpoint is based on Pi's session JSONL plus Astro metadata. Some
providers or Pi paths do not persist reasoning into `pi_session_jsonl` as native `thinking` blocks,
and they also do not leave literal `<think>...</think>` text in assistant messages.

Session `52` showed this exact case:

```python
session_52_checkpoint_observation = {
    "backend_checkpoint_exists": True,
    "assistant_messages": 20,
    "pi_thinking_blocks": 0,
    "non_empty_pi_thinking_blocks": 0,
    "text_blocks_with_think_tags": 0,
}
```

That means Astro cannot reconstruct reasoning after reload from the checkpoint alone. The backend
stored the checkpoint it received, but the checkpoint did not contain any reasoning signal.

Persisting the full frontend history in the backend is not the right fix. That would create two
authoritative transcript stores:

- Pi JSONL for runtime continuity
- backend frontend-history JSON for UI hydration

The fix should preserve only the missing semantic signal: whether a projected assistant answer had
reasoning during the live stream.

## Decision

Astro will add lightweight reasoning annotations to the existing checkpoint metadata.

Reasoning annotations live inside the checkpoint bundle's existing `astro_metadata_json`. They are
not a new backend history table, not a second transcript, and not a replacement for Pi JSONL.

The backend stores and returns the annotations as part of the existing checkpoint bundle. Astro owns
creating, updating, interpreting, and pruning those annotations.

The annotation records only that reasoning occurred. It does not store raw reasoning text unless Pi
already persisted that text somewhere else in the checkpoint, such as a native `thinking` block.

## Metadata Shape

The checkpoint bundle shape from
[`adr-emptydir-session-checkpoint-storage.md`](./adr-emptydir-session-checkpoint-storage.md) remains:

```python
checkpoint_bundle = {
    "pi_session_jsonl": str,
    "astro_metadata_json": dict,
    "thread_binding_json": dict,
    "session_overrides_json": dict | None,
}
```

Add this optional field inside `astro_metadata_json`:

```python
astro_metadata_json = {
    # existing Astro metadata fields...
    "history_annotations": history_annotations,
}
```

Exact annotation shape:

```python
history_annotations = {
    "version": 1,
    "assistant_messages": [
        {
            "assistant_ordinal": int,
            "pi_entry_id": str | None,
            "stream_message_id": str | None,
            "had_reasoning": True,
            "reasoning_text_persisted": False,
            "reasoning_started_at": str | None,
            "reasoning_completed_at": str | None,
        },
    ],
}
```

Field meanings:

- `assistant_ordinal` is the 1-based ordinal of the assistant message in Astro's projected frontend
  history for the current active branch.
- `pi_entry_id` is the Pi JSONL message entry id when Astro or the sidecar can safely correlate it.
  It is optional because live stream events may arrive before the final Pi entry id is known.
- `stream_message_id` is the Astro SSE stream message id when available.
- `had_reasoning` must be `True` for every row in this list.
- `reasoning_text_persisted` is `False` when Astro only knows reasoning happened but does not have
  persisted reasoning text. It may be `True` only when the reasoning text also exists in
  `pi_session_jsonl` as a Pi `thinking` block or equivalent persisted content.
- timestamps are ISO strings when known.

Annotations are best-effort UI metadata. They are not required for Pi restore.

## History Projection

Astro reconstructs frontend history from the latest checkpoint in this order:

1. Parse `bundle.pi_session_jsonl`.
2. Project user and assistant text messages from the current Pi branch.
3. Preserve Pi native `thinking` blocks as structured frontend reasoning parts when they exist.
4. Split literal provider `<think>...</think>` text into structured reasoning parts when it exists.
5. Apply `astro_metadata_json.history_annotations` only when no persisted reasoning text exists for
   the projected assistant message.

When an annotation says an assistant message had reasoning but no reasoning text was persisted,
Astro returns a structured empty reasoning part:

```python
reasoning_placeholder_part = {
    "type": "reasoning",
    "text": "",
}
```

Example hydrated assistant message:

```python
hydrated_assistant_message = {
    "id": "a_12",
    "role": "assistant",
    "createdAt": "2026-04-22T09:20:01.000Z",
    "completedAt": "2026-04-22T09:20:05.000Z",
    "content": [
        {
            "type": "reasoning",
            "text": "",
        },
        {
            "type": "text",
            "text": "The final assistant answer.",
        },
    ],
}
```

The frontend must render an empty `reasoning` part as the normal collapsed thinking placeholder. It
must not parse raw `<think>` tags.

If both persisted reasoning text and an annotation exist, persisted reasoning text wins. Astro must
not duplicate the reasoning block.

## Backend Responsibilities

The backend owns durable checkpoint storage, not reasoning inference.

Backend must:

- accept `bundle.astro_metadata_json.history_annotations` as optional nested JSON metadata
- store `astro_metadata_json` with the latest checkpoint bundle exactly as submitted after normal
  JSON parsing
- include `astro_metadata_json` in the existing checkpoint bundle hash calculation
- return `history_annotations` unchanged in:
  - `GET /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint/latest/`
  - `POST /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint/restore/`
  - `POST /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint/flush/` when the response
    includes a normalized bundle
- keep validation shallow: `astro_metadata_json` must be a JSON object, and
  `history_annotations`, when present, must also be a JSON object
- continue using existing checkpoint version, lease, and bundle hash conflict behavior

Backend must not:

- infer reasoning from model output
- parse Pi assistant content to discover thinking
- store a second frontend conversation history table for this feature
- create a new endpoint for reasoning annotations
- mutate, rebase, or prune annotations during compaction normalization
- store raw reasoning text in a separate column

If backend compaction normalization rewrites `pi_session_jsonl`, the backend should preserve
`astro_metadata_json` unchanged. Astro and the sidecar own removing or rebasing stale annotations
after they accept the normalized bundle.

## Backend API Contract

No new endpoint is required.

### Flush Request

The existing flush request remains the transport:

```python
checkpoint_flush_request = {
    "holder_id": holder_id,
    "lease_token": lease_token,
    "expected_checkpoint_version": expected_checkpoint_version,
    "reason": reason,
    "bundle_hash": bundle_hash,
    "bundle": {
        "pi_session_jsonl": pi_session_jsonl,
        "astro_metadata_json": {
            # existing Astro metadata...
            "history_annotations": {
                "version": 1,
                "assistant_messages": [
                    {
                        "assistant_ordinal": 12,
                        "pi_entry_id": None,
                        "stream_message_id": "msg_mo9...",
                        "had_reasoning": True,
                        "reasoning_text_persisted": False,
                        "reasoning_started_at": "2026-04-22T09:20:01.000Z",
                        "reasoning_completed_at": "2026-04-22T09:20:04.000Z",
                    },
                ],
            },
        },
        "thread_binding_json": thread_binding_json,
        "session_overrides_json": session_overrides_json_or_none,
    },
}
```

Because `history_annotations` is inside `astro_metadata_json`, the submitted `bundle_hash` must
already include it. A change to annotations is a checkpoint bundle change and may increment
`checkpoint_version` when accepted.

### Latest and Restore Responses

The existing response shape remains:

```python
checkpoint_latest_or_restore_response = {
    "agent_session_id": agent_session_id,
    "checkpoint_version": checkpoint_version,
    "bundle_hash": bundle_hash,
    "updated_at": updated_at,
    "bundle": checkpoint_bundle,
}
```

The response's `bundle.astro_metadata_json` includes `history_annotations` when they were present in
the latest accepted checkpoint.

## Astro Responsibilities

Astro owns annotation creation and interpretation.

Astro must:

- detect live `reasoning-start`, `reasoning-delta`, and `reasoning-end` events
- record one annotation for the projected assistant message that had reasoning
- write annotations into the local Astro metadata file that becomes
  `bundle.astro_metadata_json`
- avoid storing raw reasoning text in annotations
- let the sidecar flush annotations with the normal checkpoint bundle
- use annotations only as a fallback when Pi JSONL lacks native thinking content
- return structured `reasoning` content parts from `GET /api/chat/history`
- ignore malformed or stale annotations rather than corrupting history

Astro should prefer this match order during history projection:

1. `pi_entry_id` exact match when present
2. `assistant_ordinal` fallback
3. ignore the annotation if neither matches the projected assistant message

If the active Pi branch contains compaction entries, Astro disables `assistant_ordinal` fallback and
uses only `pi_entry_id` exact matches. This avoids applying pre-compaction annotations to the wrong
post-compaction assistant message after retention changes the projected history.

## Sidecar Responsibilities

The sidecar must not invent reasoning annotations.

The sidecar only needs to:

- include the current Astro metadata file in every complete checkpoint bundle
- compute the bundle hash over the metadata that includes annotations
- flush annotation-only changes through the same lease/version path as any other checkpoint change
- preserve returned normalized bundles exactly

If compaction normalization causes local history cache deletion, the sidecar should also leave Astro
able to rebuild or rebase annotations on the next history projection.

## Frontend Responsibilities

The frontend receives structured history from Astro.

Frontend must:

- render `{ "type": "reasoning", "text": "" }` as the normal collapsed thinking placeholder
- render `{ "type": "reasoning", "text": "..." }` as persisted reasoning text using the same
  thinking UI
- render `{ "type": "text", "text": "..." }` as the final assistant answer
- never parse `<think>` tags

## Existing Sessions

This cannot recover reasoning for old checkpoints that did not store Pi `thinking` blocks, literal
thinking tags, or Astro reasoning annotations.

For those sessions, history can only show what the backend checkpoint actually contains.

Session `52` is in this category. Its checkpoint exists, but it has no persisted reasoning signal.

## Tasks

Backend:

- [x] Ensure checkpoint serializers accept `bundle.astro_metadata_json.history_annotations` as
      optional nested JSON.
- [x] Ensure checkpoint hash calculation includes `history_annotations` because it is part of
      `astro_metadata_json`.
- [x] Ensure `checkpoint/latest/`, `checkpoint/restore/`, and `checkpoint/flush/` return
      `astro_metadata_json` unchanged.
- [x] Keep backend validation shallow and avoid workflow validation of annotation semantics.
- [x] Do not add a separate frontend history table or reasoning endpoint.

Astro:

- [x] Record reasoning annotations while live stream reasoning events are observed.
- [x] Persist annotations into the local Astro metadata file before terminal checkpoint markers.
- [x] Update sidecar bundle building if needed so metadata-only changes are treated as dirty.
- [x] Apply annotations during backend checkpoint history projection when Pi JSONL has no persisted
      reasoning text.
- [x] Ignore or prune stale annotations after compaction normalization.
- [x] Verify native Pi `thinking`, literal `<think>` text, and annotation-only reasoning.

Frontend:

- [ ] Render empty `reasoning` history parts as collapsed thinking placeholders.
- [ ] Keep rendering non-empty `reasoning` parts with the thinking UI.
