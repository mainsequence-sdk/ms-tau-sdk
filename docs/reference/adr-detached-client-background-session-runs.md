# ADR: Detached Clients Do Not Cancel Session Runs

## Status

Proposed. Not implemented.

## Context

Astro streams assistant output to the browser over SSE, but the durable session is no longer the
browser connection. The durable session is the backend `AgentSession.id` plus the latest backend
checkpoint bundle described in
[`adr-emptydir-session-checkpoint-storage.md`](./adr-emptydir-session-checkpoint-storage.md).

Today the stream runtime still treats browser disconnect as a terminal event for the active Pi
process. If the browser closes in the middle of an assistant response, Astro stops Pi, writes an
error-shaped local history record, and asks the sidecar to finalize a checkpoint as an error.

That behavior is wrong for backend-backed sessions. Closing the browser should detach the client
from the transport. It should not cancel the active agent run.

The backend must also be able to tell that a session is actively working even when no browser is
connected. The active runtime communication with the backend is the checkpoint lease renewal and
checkpoint flush cycle, not the SSE connection.

## Decision

Astro will separate browser transport lifecycle from agent run lifecycle.

Browser close means:

```python
client_detach = {
    "client_attached": False,
    "cancel_pi": False,
    "write_stream_error": False,
    "release_checkpoint_lease": False,
}
```

The active Pi process continues until it naturally finishes or fails.

Astro continues to:

- read Pi stdout/stderr
- record stream chunks into local runtime cache while the pod is alive
- keep the checkpoint lease renewed
- let the sidecar flush periodic checkpoints
- write a terminal checkpoint marker when Pi finishes or fails

The backend knows the session is working from:

- `AgentSession.status = "running"` while an active runtime lease is held for a Pi run
- the unexpired checkpoint lease row and its heartbeat
- accepted checkpoint flushes from the sidecar

The browser connection is not part of backend session ownership.

## Runtime Flow

### Start or resume a run

1. Frontend sends `POST /api/chat`.
2. Astro resolves the backend `AgentSession.id`.
3. Astro acquires or renews the checkpoint lease with `lease_purpose = "runtime_run"`.
4. Backend records the session as working.
5. Astro restores local files from the backend checkpoint if needed.
6. Astro launches Pi with the local `--session` JSONL file.
7. Astro streams chunks to the browser while the browser remains connected.

### Browser closes during the run

When the browser disconnects:

```python
client_detached_runtime_action = {
    "log_event": "stream_client_detached",
    "set_client_attached": False,
    "continue_pi_process": True,
    "continue_checkpoint_lease_renewal": True,
    "continue_local_history_reducer": True,
    "skip_future_sse_writes": True,
}
```

Astro must not:

- kill Pi
- mark the session as failed
- write `client_stream_aborted` into frontend history
- write an error checkpoint marker only because the browser detached
- stop checkpoint lease renewal
- release the checkpoint lease

### Pi finishes after browser detach

When Pi finishes successfully:

1. Astro records the final assistant state locally.
2. Astro writes a checkpoint marker with reason `finish`.
3. Sidecar flushes the full checkpoint bundle to the backend with reason `stream_finish`.
4. Backend accepts the flush, stores the latest bundle, marks the session not working, and sets
   `AgentSession.status = "completed"`.
5. Sidecar releases the checkpoint lease.

No SSE `[DONE]` is written if the browser is already detached.

### Pi fails after browser detach

When Pi or the runtime fails:

1. Astro records the real runtime/provider/Pi error locally.
2. Astro writes a checkpoint marker with reason `error`.
3. Sidecar flushes the full checkpoint bundle to the backend with reason `stream_error`.
4. Backend accepts the flush, stores the latest bundle, marks the session not working, and sets
   `AgentSession.status = "error"`.
5. Sidecar releases the checkpoint lease.

The durable error must be the real terminal error. It must not be replaced with "browser
disconnected".

## Backend Contract

This ADR requires backend behavior and API changes. It does not require a new backend checkpoint
model. It extends the behavior of the existing `AgentSession`, checkpoint lease, and checkpoint
flush contracts.

### Backend runtime state rules

The backend must expose a response field named `working`.

`working` is not stored. It is computed when returning lease responses and runtime-state responses.
It answers one question:

```python
working_question = "Does the backend currently believe an Astro runtime is executing this session?"
```

The existing checkpoint lease row must persist the lease purpose:

```python
agent_session_lease_additive_field = {
    "lease_purpose": "runtime_run" | "read_restore",
}
```

`runtime_run` means the lease holder is allowed to mutate the local Pi session file for a user chat
turn and flush those mutations back to the backend.

`read_restore` means the lease holder is only restoring checkpoint state for a read endpoint such as
history or session-insights. It must not make the session working.

Exact implementation:

```python
def compute_agent_session_working(agent_session, lease, now):
    if agent_session.status != "running":
        return False

    if agent_session.ended_at is not None:
        return False

    if lease is None:
        return False

    if lease.lease_purpose != "runtime_run":
        return False

    if lease.expires_at <= now:
        return False

    return True
```

The backend must call this helper only after loading the current `AgentSession` row and the current
checkpoint lease row for the same `agent_session_id`.

Backend must use this value in:

```python
backend_working_response_usage = {
    "checkpoint_lease_acquire_response": "working",
    "checkpoint_lease_renew_response": "working",
    "runtime_state_response": "working",
}
```

Backend must not use browser state, SSE state, frontend tabs, or local Astro process memory to
compute `working`.

### Backend endpoint behavior

The backend must implement these state transitions.

#### `checkpoint_lease/acquire/`

```python
def checkpoint_lease_acquire(agent_session_id, request):
    agent_session = lock_agent_session(agent_session_id)
    existing_lease = lock_checkpoint_lease(agent_session_id)

    if existing_lease and existing_lease.expires_at > now():
        return error_response(
            status=409,
            error_code="checkpoint_lease_already_held",
        )

    lease = create_or_replace_checkpoint_lease(
        agent_session_id=agent_session.id,
        holder_id=request["holder_id"],
        lease_purpose=request["lease_purpose"],
        ttl_seconds=request["ttl_seconds"],
    )

    if request["lease_purpose"] == "runtime_run":
        agent_session.status = "running"
        agent_session.ended_at = None
        agent_session.error_detail = ""
        agent_session.save(update_fields=["status", "ended_at", "error_detail"])

    working = compute_agent_session_working(agent_session, lease, now())
    return checkpoint_lease_response(lease=lease, working=working)
```

If `lease_purpose = "read_restore"`, the backend creates the lease but must not change
`AgentSession.status`, `ended_at`, or `error_detail`.

#### `checkpoint_lease/renew/`

```python
def checkpoint_lease_renew(agent_session_id, request):
    agent_session = lock_agent_session(agent_session_id)
    lease = lock_checkpoint_lease(agent_session_id)

    if lease is None:
        return error_response(status=409, error_code="checkpoint_lease_missing")

    if lease.holder_id != request["holder_id"] or lease.lease_token != request["lease_token"]:
        return error_response(status=409, error_code="checkpoint_lease_token_mismatch")

    if lease.lease_purpose != request["lease_purpose"]:
        return error_response(status=409, error_code="checkpoint_lease_purpose_mismatch")

    if lease.expires_at <= now():
        return error_response(status=409, error_code="checkpoint_lease_expired")

    lease.heartbeat_at = now()
    lease.expires_at = now() + seconds(request["ttl_seconds"])
    lease.save(update_fields=["heartbeat_at", "expires_at"])

    if lease.lease_purpose == "runtime_run":
        agent_session.status = "running"
        agent_session.save(update_fields=["status"])

    working = compute_agent_session_working(agent_session, lease, now())
    return checkpoint_lease_response(lease=lease, working=working)
```

#### `checkpoint/flush/`

For non-terminal flushes:

```python
non_terminal_flush_reasons = {"periodic", "compaction", "shutdown"}
```

If `agent_session_terminal_state is None`, the backend stores the checkpoint according to the
existing checkpoint ADR and must not change `AgentSession.status`, `ended_at`, or `error_detail`.

For terminal flushes:

```python
terminal_flush_reasons = {
    "stream_finish": "completed",
    "stream_error": "error",
    "shutdown": "canceled",
}
```

After the backend validates lease, checkpoint version, bundle hash, and bundle contents, it must
store or no-op the checkpoint first. Then it must apply the terminal session update:

```python
def apply_terminal_session_state(agent_session, terminal_state):
    agent_session.status = terminal_state["status"]
    agent_session.ended_at = now()
    agent_session.error_detail = terminal_state["error_detail"] or ""
    agent_session.save(update_fields=["status", "ended_at", "error_detail"])
```

This terminal update must happen even when the checkpoint write is a valid no-op. It must not happen
when the checkpoint flush is rejected.

#### `checkpoint_lease/release/`

Release deletes or clears the matching lease. Release does not set `AgentSession.status`.

```python
def checkpoint_lease_release(agent_session_id, request):
    lease = lock_checkpoint_lease(agent_session_id)

    if lease is None:
        return success_response(released=False)

    if lease.holder_id != request["holder_id"] or lease.lease_token != request["lease_token"]:
        return error_response(status=409, error_code="checkpoint_lease_token_mismatch")

    delete_checkpoint_lease(lease)
    return success_response(released=True)
```

Terminal state belongs to `checkpoint/flush/`, not release. This prevents a release from pretending
that a run completed when no final checkpoint was accepted.

#### `runtime_state/`

```python
def runtime_state(agent_session_id):
    agent_session = get_agent_session(agent_session_id)
    lease = get_checkpoint_lease(agent_session_id)
    checkpoint = get_latest_checkpoint(agent_session_id)

    return {
        "agent_session_id": agent_session.id,
        "thread_id": agent_session.thread_id,
        "status": agent_session.status,
        "working": compute_agent_session_working(agent_session, lease, now()),
        "active_lease": serialize_public_lease(lease),
        "checkpoint": serialize_checkpoint_summary(checkpoint),
    }
```

`serialize_public_lease(lease)` must not include `lease_token`.

### Status meaning

`AgentSession.status = "running"` means a runtime run has been started and has not yet accepted a
terminal checkpoint flush. It does not mean the browser is connected.

`AgentSession.status = "completed"` means the latest assistant run finished successfully and the
session can receive another user message later.

`AgentSession.status = "error"` means the latest assistant run failed. The session may still be
resumed later by starting a new run.

`AgentSession.status = "canceled"` means the latest assistant run was manually stopped by a user
or operator. The session may still be resumed later by starting a new run.

### Impact of `AgentSession.status`

This ADR makes `AgentSession.status` represent the current runtime state of the existing session,
not a permanent archive state.

Impact:

- A session can move from `"completed"` back to `"running"` when the user sends another message and
  Astro successfully acquires a `runtime_run` lease.
- A session can move from `"error"` back to `"running"` when the user retries or continues the same
  session and Astro successfully acquires a `runtime_run` lease.
- A session can move from `"canceled"` back to `"running"` when the user continues the same session
  and Astro successfully acquires a `runtime_run` lease.
- `"completed"` means "the latest run completed", not "this chat is closed forever".
- `"error"` means "the latest run failed", not "this session id can never be used again".
- `"canceled"` means "the latest run was intentionally stopped", not "this session id can never be
  used again".
- `ended_at` is the end time of the latest completed, failed, or canceled run. It is cleared when a
  new `runtime_run` starts on the same session.
- `error_detail` is the latest run error or cancellation detail. It is cleared when a new
  `runtime_run` starts.

Backend query code and UI code must not use `status = "completed"` as "hide this session from
active conversations" or "make this session immutable". The session remains resumable as long as the
backend accepts a new runtime lease for it.

If the backend needs immutable per-run history later, that should be modeled separately as a run
record. This ADR intentionally avoids adding that model in the first implementation.

### Lease acquire and renew

Existing checkpoint lease endpoints remain the backend authority for runtime ownership:

```http
POST /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint_lease/acquire/
POST /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint_lease/renew/
```

Add `lease_purpose` to both request bodies.

```python
checkpoint_lease_acquire_request = {
    "holder_id": str,
    "ttl_seconds": int,
    "lease_purpose": "runtime_run" | "read_restore",
}
```

```python
checkpoint_lease_renew_request = {
    "holder_id": str,
    "lease_token": str,
    "ttl_seconds": int,
    "lease_purpose": "runtime_run" | "read_restore",
}
```

Value rules:

- Astro must send `lease_purpose = "runtime_run"` only before launching or while running a Pi child
  process for a user chat turn.
- Astro must send `lease_purpose = "read_restore"` for read-only checkpoint hydration, such as
  history or session-insights hydration.
- Backend must not mark an `AgentSession` as running for `read_restore`.
- Backend must update `AgentSession.status` only for successful `runtime_run` acquire or renew.
- Backend must not store `working`; it must compute and return `working` from
  `compute_agent_session_working(...)`.

Successful `runtime_run` acquire behavior:

```python
agent_session_update_on_runtime_lease_acquire = {
    "status": "running",
    "ended_at": None,
    "error_detail": "",
}
```

Successful `runtime_run` renew behavior:

```python
agent_session_update_on_runtime_lease_renew = {
    "status": "running",
}
```

The backend must continue to update the lease heartbeat and expiry exactly as defined by the
emptyDir checkpoint ADR.

The lease response must include enough state for Astro logs and frontend diagnostics:

```python
checkpoint_lease_response = {
    "agent_session_id": int,
    "holder_id": str,
    "lease_token": str,
    "lease_expires_at": str,
    "checkpoint_version": int,
    "bundle_hash": str,
    "agent_session_status": "running" | "completed" | "error" | "canceled" | str,
    "working": bool,
}
```

`working` is computed by the backend using `compute_agent_session_working(...)`.

### Terminal checkpoint flush

The existing checkpoint flush endpoint remains the authority for storing the final bundle:

```http
POST /orm/api/agents/v1/sessions/{agent_session_id}/checkpoint/flush/
```

Extend the existing flush request with `agent_session_terminal_state`.

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
    "agent_session_terminal_state": {
        "status": "completed" | "error" | "canceled",
        "error_code": str | None,
        "error_detail": str | None,
    } | None,
}
```

Value rules:

- For `reason = "stream_finish"`, Astro/sidecar must send:

  ```python
  agent_session_terminal_state = {
      "status": "completed",
      "error_code": None,
      "error_detail": None,
  }
  ```

- For `reason = "stream_error"`, Astro/sidecar must send:

  ```python
  agent_session_terminal_state = {
      "status": "error",
      "error_code": str | None,
      "error_detail": str,
  }
  ```

- For cancellation finalization, Astro/sidecar must send the normal shutdown flush with:

  ```python
  reason = "shutdown"
  agent_session_terminal_state = {
      "status": "canceled",
      "error_code": "session_cancelled_by_user",
      "error_detail": "Session canceled by user.",
  }
  ```

- For `reason = "periodic"` or `reason = "compaction"`, `agent_session_terminal_state` must be
  `None`.
- For `reason = "shutdown"`, `agent_session_terminal_state` must be `None` unless Astro has already
  determined the Pi run actually ended with success, error, or user cancellation.
- Browser detach alone must never produce `agent_session_terminal_state`.

Accepted terminal flush behavior:

```python
agent_session_update_on_terminal_flush = {
    "status": agent_session_terminal_state["status"],
    "ended_at": backend_now,
    "error_detail": agent_session_terminal_state["error_detail"] or "",
}
```

If the terminal flush is a checkpoint no-op because the submitted bundle is already stored, the
backend must still apply the terminal `AgentSession` update when the lease and checkpoint version are
valid.

Rejected terminal flush behavior:

```python
agent_session_update_on_rejected_terminal_flush = None
```

The backend must not mark the session completed or failed when the flush is rejected.

### Runtime state read endpoint

Add a backend read endpoint for observability and frontend polling:

```http
GET /orm/api/agents/v1/sessions/{agent_session_id}/runtime_state/
```

Response body:

```python
agent_session_runtime_state_response = {
    "agent_session_id": int,
    "thread_id": str,
    "status": "running" | "completed" | "error" | "canceled" | str,
    "working": bool,
    "active_lease": {
        "holder_id": str,
        "heartbeat_at": str,
        "expires_at": str,
        "expired": bool,
    } | None,
    "cancel_requested": bool,
    "cancellation": {
        "cancellation_id": str,
        "requested_at": str,
        "requested_by_user_id": int | None,
        "requested_by_username": str | None,
        "reason": str,
        "message": str | None,
    } | None,
    "checkpoint": {
        "checkpoint_version": int | None,
        "bundle_hash": str,
        "last_flush_reason": str | None,
        "last_flush_at": str | None,
    },
}
```

Rules:

- The backend must not return `lease_token` from this read endpoint.
- `working` must be `False` when the lease is missing or expired, even if `AgentSession.status` is
  still `"running"`.
- This endpoint is read-only. It must not acquire, renew, release, or mutate leases.

## Astro Contract

### Runtime state

Astro must maintain separate concepts:

```python
astro_stream_runtime_state = {
    "client_attached": bool,
    "run_finished": bool,
    "run_failed": bool,
    "checkpoint_lease_active": bool,
}
```

`client_attached = False` must not imply `run_finished = True`.

### SSE writes

Astro must continue processing Pi output after browser detach.

The output path becomes:

```python
pi_output_path = {
    "parse_pi_event": True,
    "update_local_history_cache": True,
    "update_checkpoint_metadata": True,
    "write_sse_if_client_attached": True,
}
```

SSE writes are conditional:

```python
if client_attached and not response_closed:
    write_sse_chunk(chunk)
```

History/cache/checkpoint metadata writes are not conditional on the browser.

### Active run registry

Astro must track active runs by backend `AgentSession.id`:

```python
active_run_registry_entry = {
    "agent_session_id": int,
    "thread_id": str,
    "holder_id": str,
    "lease_token": str,
    "client_attached": bool,
    "started_at": str,
    "last_pi_event_at": str | None,
    "last_checkpoint_version": int | None,
    "last_bundle_hash": str | None,
}
```

If a new `POST /api/chat` arrives for an `AgentSession.id` that already has an active run, Astro
must not launch a second Pi process for that session.

First implementation behavior:

```python
session_run_already_active_error = {
    "type": "error",
    "error": "This session already has an active assistant run.",
    "error_source": "runtime",
    "status": 409,
    "error_code": "session_run_already_active",
    "error_detail": "Wait for the active run to finish, then retry.",
}
```

Live reattach to an already-running Pi process can be added later. It is not required for the first
implementation as long as history and runtime-state polling work.

### Checkpoint markers

Browser detach does not write a checkpoint marker.

Terminal Pi completion writes the marker:

```python
checkpoint_marker_on_success = {
    "session_id": str,
    "agent_session_id": int,
    "thread_id": str,
    "reason": "finish",
    "lease_holder_id": str,
    "lease_token": str,
    "checkpoint_version": int,
    "bundle_hash": str,
    "agent_session_terminal_state": {
        "status": "completed",
        "error_code": None,
        "error_detail": None,
    },
}
```

Terminal Pi/runtime/provider failure writes the marker:

```python
checkpoint_marker_on_error = {
    "session_id": str,
    "agent_session_id": int,
    "thread_id": str,
    "reason": "error",
    "lease_holder_id": str,
    "lease_token": str,
    "checkpoint_version": int,
    "bundle_hash": str,
    "agent_session_terminal_state": {
        "status": "error",
        "error_code": str | None,
        "error_detail": str,
    },
}
```

Manual user cancellation writes the marker:

```python
checkpoint_marker_on_cancel = {
    "session_id": str,
    "agent_session_id": int,
    "thread_id": str,
    "reason": "cancel",
    "lease_holder_id": str,
    "lease_token": str,
    "checkpoint_version": int,
    "bundle_hash": str,
    "agent_session_terminal_state": {
        "status": "canceled",
        "error_code": "session_cancelled_by_user",
        "error_detail": "Session canceled by user.",
    },
}
```

The sidecar uses the existing backend shutdown flush for this marker:

```python
checkpoint_marker_reason_map = {
    "finish": "stream_finish",
    "error": "stream_error",
    "cancel": "shutdown",
}
```

## Frontend Behavior

If the user returns after the detached run finished, the frontend loads:

```http
GET backend-owned chat history for {runtime_session_id}
```

The history service returns the completed assistant answer from durable session state.

If the user returns while the run is still active, the frontend can poll:

```http
GET backend-owned chat history for {runtime_session_id}
```

and optionally:

```http
GET /orm/api/agents/v1/sessions/{agent_session_id}/runtime_state/
```

until `working = False`.

Frontend history must not show a fake browser-disconnect assistant error. The browser closing is a
transport detach, not an assistant response.

## Manual Session Cancellation

Detached runs need an explicit user cancellation path. Browser close must not cancel the run, but a
user action such as pressing "stop" must be able to stop the active run and unblock the session.

The frontend must call Astro, not the backend:

```http
POST /api/chat/session/cancel
Content-Type: application/json
```

```python
cancel_session_request = {
    "runtime_session_id": str,
    "thread_id": str | None,
    "reason": "user_requested" | None,
    "message": str | None,
}
```

Astro must resolve `runtime_session_id` to the backend `AgentSession.id`. `thread_id` is only
correlation and must not be used as the session identity.

Astro response:

```python
cancel_session_response = {
    "ok": True,
    "session_id": str,
    "agent_session_id": int,
    "state": "cancelling" | "cancel_requested" | "not_running",
    "working": bool,
    "cancellation_id": str | None,
    "message": str,
}
```

### Astro to backend cancellation request

Astro must always tell the backend that cancellation was requested before it mutates the local Pi
process. This makes cancellation visible even if the request hits a different Astro replica from the
one currently holding the runtime lease.

```http
POST /orm/api/agents/v1/sessions/{agent_session_id}/runtime_cancel_request/
Content-Type: application/json
```

```python
backend_runtime_cancel_request = {
    "requested_by_holder_id": str | None,
    "reason": "user_requested" | str | None,
    "message": str | None,
}
```

`requested_by_holder_id`, `reason`, and `message` are optional. Astro must not send
`requested_by_user`; the backend derives the requester from the authenticated user.

Backend response:

```python
backend_runtime_cancel_response = {
    "agent_session_id": int,
    "status": "running" | "completed" | "error" | "canceled" | str,
    "runtime_state": "working" | "stale" | str,
    "working": bool,
    "cancel_state": "not_running" | "requested",
    "cancel_requested": bool,
    "cancellation_id": str | None,
    "active_lease": {
        "holder_id": str,
        "lease_purpose": "runtime_run" | str,
        "heartbeat_at": str | None,
        "expires_at": str,
        "expired": bool,
    } | None,
}
```

Backend behavior:

1. Load the current `AgentSession`, checkpoint lease, and computed runtime state.
2. If `working = False`, return `cancel_state = "not_running"` and do not mutate
   `AgentSession.status`.
3. If `working = True`, store cancellation intent on the active checkpoint lease row.
4. Repeated cancel requests on the same active lease must return the same `cancellation_id`.
5. Do not set `AgentSession.status = "canceled"` in this endpoint.

Cancellation intent belongs to the active lease row:

```python
agent_session_lease_cancellation_fields = {
    "cancel_requested": bool,
    "cancel_requested_at": datetime | None,
    "cancel_requested_by_user_id": int | None,
    "cancel_requested_by_username": str | None,
    "cancel_reason": "user_requested" | str,
    "cancel_message": str,
    "cancellation_id": str,
}
```

The backend must include the cancellation intent in successful `runtime_run` lease renew responses:

```python
checkpoint_lease_renew_response = {
    # existing lease response fields...
    "cancel_requested": bool,
    "cancellation": {
        "cancellation_id": str,
        "requested_at": str,
        "requested_by_user_id": int | None,
        "requested_by_username": str | None,
        "reason": str,
        "message": str | None,
    } | None,
}
```

The backend must also include the cancellation intent in `runtime_state/`:

```python
runtime_state_cancellation = {
    "cancel_requested": bool,
    "cancellation": {
        "cancellation_id": str,
        "requested_at": str,
        "requested_by_user_id": int | None,
        "requested_by_username": str | None,
        "reason": str,
        "message": str | None,
    } | None,
}
```

### Active holder cancellation

Only the Astro runtime that owns the active local Pi child process can actually stop that process.

If the cancel request hits the active holder:

1. Astro calls backend `runtime_cancel_request/`.
2. Astro marks the active run as cancelling.
3. Astro stops Pi with `SIGTERM`.
4. Astro keeps the checkpoint lease renewal alive until the Pi child exits or is force killed.
5. If Pi does not exit within the configured grace period, Astro sends `SIGKILL`.
6. Astro records a terminal cancellation event locally.
7. Astro writes a checkpoint marker with local reason `cancel`.
8. Sidecar sends the normal checkpoint flush with `reason = "shutdown"`.
9. Sidecar sends `agent_session_terminal_state.status = "canceled"` in the shutdown flush.
10. Backend accepts the shutdown terminal flush, stores the latest checkpoint, sets
    `AgentSession.status = "canceled"`, sets `ended_at = now()`, stores
    `error_detail = "Session canceled by user."`, and then the sidecar releases the lease with
    release reason `runtime_canceled`.

If the cancel request does not hit the active holder:

1. Astro calls backend `runtime_cancel_request/`.
2. Astro returns `state = "cancel_requested"`.
3. The active holder sees `cancel_requested = True` on its next successful lease renew.
4. The active holder performs the active holder cancellation flow above.

This means cancellation latency across replicas is bounded by the runtime lease renewal interval.

### Active SSE behavior during cancellation

If the browser is still attached when cancellation completes, Astro sends a terminal SSE error-like
chunk for compatibility with the current stream protocol:

```python
canceled_stream_event = {
    "type": "error",
    "error": "Session canceled by user.",
    "error_source": "runtime",
    "status": 499,
    "error_code": "session_cancelled_by_user",
    "error_detail": "Session canceled by user.",
}
```

This SSE shape is only a transport compatibility detail. The backend terminal session state is
`canceled`, not `error`.

## Failure Boundaries

This ADR changes browser-close behavior and adds an explicit manual cancellation path.

If the Astro pod dies while Pi is still running, Pi dies with the pod. `emptyDir` is pod-scoped and
cannot keep a running process alive after pod termination.

The expected recovery after pod death is:

1. Last accepted backend checkpoint remains durable.
2. Backend lease eventually expires if it was not released.
3. A new Astro pod can acquire the expired lease and resume from the last checkpoint.

Keeping a run alive across pod death requires a durable worker/runtime architecture and is outside
this ADR.

## Consequences

### Positive

- browser close no longer destroys active assistant work
- backend can show that a session is still working without an attached frontend
- checkpoint lease renewal remains the single writer authority
- final history hydration can show the completed answer after the user returns
- real Pi/provider/backend failures remain distinguishable from browser disconnects

### Negative

- detached runs can consume compute after the user leaves
- Astro needs an active run registry to prevent duplicate Pi processes for one session
- first implementation does not provide live SSE reattach to an already-running Pi process
- pod death still terminates the active run

## Non-Goals

- streaming assistant output from the backend
- moving Pi execution into the backend
- keeping Pi alive across pod termination
- storing every streamed SSE chunk in Postgres
- adding a second frontend-history transcript table
- exposing backend checkpoint lease tokens to the frontend

## Backend Tasks

- [x] Add `lease_purpose` to the existing checkpoint lease row.
- [x] Add `lease_purpose` to checkpoint lease acquire and renew requests.
- [x] Ensure `lease_purpose = "read_restore"` never changes `AgentSession.status`.
- [x] On successful `runtime_run` lease acquire, set `AgentSession.status = "running"`,
      `ended_at = None`, and `error_detail = ""`.
- [x] On successful `runtime_run` lease renew, keep `AgentSession.status = "running"`.
- [x] Extend checkpoint lease responses with `agent_session_status` and `working`.
- [x] Add `agent_session_terminal_state` to checkpoint flush requests.
- [x] On accepted `stream_finish` terminal flush, set `AgentSession.status = "completed"` and
      `ended_at = now()`.
- [x] On accepted `stream_error` terminal flush, set `AgentSession.status = "error"`,
      `ended_at = now()`, and persist the terminal `error_detail`.
- [x] Apply terminal `AgentSession` updates even when the checkpoint bundle is a valid no-op.
- [x] Do not apply terminal `AgentSession` updates when the checkpoint flush is rejected.
- [x] Add `GET /orm/api/agents/v1/sessions/{agent_session_id}/runtime_state/`.
- [x] Ensure the runtime-state endpoint never returns `lease_token`.
- [x] Add `AgentSession.status = "canceled"` if the backend enum does not already support it.
- [x] Add cancellation intent fields to the active checkpoint lease row.
- [x] Add `POST /orm/api/agents/v1/sessions/{agent_session_id}/runtime_cancel_request/`.
- [x] Ensure `runtime_cancel_request/` never directly sets terminal `AgentSession.status`.
- [x] Include cancellation intent in successful `runtime_run` lease renew responses.
- [x] Include cancellation intent in `runtime_state/`.
- [x] Finalize user cancellation through the existing shutdown checkpoint flush path.
- [x] On accepted shutdown terminal flush with `status = "canceled"`, set `AgentSession.status = "canceled"`,
      `ended_at = now()`, and persist the cancellation detail.

## Astro Tasks

- [x] Split client transport state from Pi run state in the stream context.
- [x] Change browser `close` handling to log `stream_client_detached` only.
- [x] Remove Pi termination from browser disconnect handling.
- [x] Remove `client_stream_aborted` local history writes from browser disconnect handling.
- [x] Keep checkpoint lease renewal alive after browser disconnect.
- [x] Continue reading and reducing Pi output after browser disconnect.
- [x] Guard SSE writes with `client_attached`, while keeping history/checkpoint updates active.
- [x] Add an active run registry keyed by backend `AgentSession.id`.
- [x] Reject duplicate `POST /api/chat` launches for sessions with an active Pi run.
- [x] Send `lease_purpose = "runtime_run"` for Pi-launch leases and renewals.
- [x] Send `lease_purpose = "read_restore"` for read-only checkpoint hydration leases.
- [x] Include `agent_session_terminal_state` in terminal checkpoint markers.
- [x] Update the sidecar to forward `agent_session_terminal_state` in terminal flush requests.
- [x] Add `POST /api/chat/session/cancel`.
- [x] Make the Astro cancel endpoint call backend `runtime_cancel_request/` before stopping Pi.
- [x] Track cancellation state in the active-run registry.
- [x] Stop Pi on local active-holder cancellation without releasing the checkpoint lease early.
- [x] Add SIGTERM grace timeout followed by SIGKILL for canceled Pi children.
- [x] Make lease renewal react to backend `cancel_requested = True`.
- [x] Write local checkpoint marker reason `cancel` for manual cancellation.
- [x] Update the sidecar to finalize cancellation through the backend `shutdown` checkpoint flush.
- [x] Send `agent_session_terminal_state.status = "canceled"` for cancellation finalization flushes.
- [x] Return `state = "cancel_requested"` when the cancel request is recorded but the active holder
      is another Astro runtime.
- [ ] Verify browser close mid-response lets Pi finish and sidecar flush `stream_finish`.
- [ ] Verify backend runtime state reports `working = True` while the browser is closed and Pi is
      still running.
- [ ] Verify reopened history shows the completed assistant response and no browser-disconnect
      error.
- [ ] Verify manual cancellation stops an active local run, flushes `shutdown` with terminal state
      `canceled`, releases the
      lease, and unblocks the session for the next user message.
- [ ] Verify cross-replica cancellation is observed through lease renew and then finalized by the
      active holder.
