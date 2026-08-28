# ADR 48: Batched Tau Session Persistence and Two-Phase Turn Settlement

Status: Accepted
Date: 2026-08-27
Implementation Status: Implemented locally across Astro Tau and tdag-django

## Context

Tau models a durable session as an append-only tree. A new session starts with a
SessionInfoEntry, ModelChangeEntry, and ThinkingLevelChangeEntry. During a turn, every completed
user, assistant, and tool-result message is persisted as a MessageEntry followed by a LeafEntry
identifying the active branch.

That representation is inexpensive with local JSONL storage. It is not an acceptable remote
transport contract when Astro maps every logical entry to a separate Django request:

~~~text
Tau message
  -> POST /agent-sessions/{uid}/entries/append/
Tau leaf
  -> POST /agent-sessions/{uid}/entries/append/
~~~

The deployed development path demonstrated the amplification. One agent-discovery turn generated
30 entry-appending requests whose measured dependency time totaled 248.698 seconds. The complete
Tau turn took 248.723 seconds. Individual calls through the development backend path took
approximately 3 to 15 seconds.

ADR 45 moved individual writes off the provider critical path using an in-memory ordered queue. It
retained flush() as the durability boundary and withheld Tau's terminal agent_settled event until
every queued entry had been written individually:

~~~text
model and tools settle
  -> wait for N serialized backend requests
  -> emit agent_settled
  -> emit assistant-ui finish and [DONE]
~~~

The model can therefore finish an answer minutes before the UI is told that the turn has finished.
The current runtime-state contract compounds this problem. Django treats any valid runtime_run
lease on a running session as working, while Astro deliberately retains and renews the lease for an
idle loaded runtime, normally for up to 15 minutes.

Logs also obscure what is occurring:

- 201 Created from entries/append/ means a TauAgentSessionEntry was created inside an existing
  session, not that an AgentSession was created;
- raw httpx logs and dependency.call.completed log the same request twice;
- generic message events are incorrectly counted as model calls; and
- MCP background transport events can lose request, turn, and session correlation.

This ADR defines the cross-repository contract required to make durable Tau sessions responsive
without discarding their ordered, resumable history.

## Decision

### 1. Preserve Tau's logical entry model

Astro and Django continue to persist Tau's canonical logical sequence. MessageEntry, LeafEntry,
configuration changes, compaction entries, labels, and other Tau entry types remain individually
addressable records with stable IDs.

The first implementation does not remove LeafEntry or redefine Tau's tree semantics. The urgent
problem is one remote transaction per logical entry. Collapsing leaf pointers into mutable session
state may be evaluated separately after branch, resume, compaction, and SDK consumers are audited.

### 2. Add an atomic batch-append backend operation

tdag-django adds the canonical Tau operation:

~~~http
POST /api/v1/agent-sessions/{agent_session_uid}/entries/append-batch/
~~~

Request:

~~~json
{
  "lease_token": "6a338b15-6e8c-4fc9-b04d-c67f0fa3d862",
  "expected_sequence": 41,
  "entries": [
    {
      "idempotency_key": "tau-entry-id-1",
      "entry": {
        "id": "tau-entry-id-1",
        "type": "message",
        "parent_id": "previous-entry-id"
      }
    },
    {
      "idempotency_key": "tau-entry-id-2",
      "entry": {
        "id": "tau-entry-id-2",
        "type": "leaf",
        "parent_id": "tau-entry-id-1",
        "entry_id": "tau-entry-id-1"
      }
    }
  ]
}
~~~

Response:

~~~json
{
  "entries": [
    {
      "sequence": 41,
      "entry_type": "message",
      "entry_json": {},
      "idempotency_key": "tau-entry-id-1"
    },
    {
      "sequence": 42,
      "entry_type": "leaf",
      "entry_json": {},
      "idempotency_key": "tau-entry-id-2"
    }
  ],
  "next_sequence": 43,
  "created_count": 2,
  "replayed": false
}
~~~

The backend operation must:

1. lock the AgentSession and runtime lease once;
2. validate the Tau harness, active runtime_run lease, token, and expiration once;
3. enforce a non-empty bounded entry count and aggregate byte limit;
4. validate every entry against the canonical Tau schema;
5. require every entry.id to equal its idempotency_key;
6. reject duplicate IDs or idempotency keys inside the request;
7. validate the starting expected_sequence;
8. allocate contiguous sequence numbers;
9. insert every new row in one database transaction; and
10. return no partially committed batch.

Limits are settings-backed and explicit. `AGENT_RUNTIME_ENTRY_MAX_BYTES` continues to cap each
entry at 4 MiB. New settings `AGENT_RUNTIME_ENTRY_BATCH_MAX_ENTRIES` and
`AGENT_RUNTIME_ENTRY_BATCH_MAX_BYTES` default to 100 entries and 8 MiB of canonical UTF-8 entry
JSON respectively. The aggregate limit is the sum of canonical serialized `entry` values; normal
HTTP request-size protection remains independent.

Batching does not introduce stronger tree-reference rules than the current single-entry endpoint.
The JSON schema validates the shape of `parent_id`, `entry_id`, `branch_root_id`, and
`replaces_entry_ids`, but the first batch implementation does not turn those fields into relational
foreign keys or reject a reference that single append currently accepts. Adding referential
integrity is a separate Tau-history decision.

### 3. Define batch idempotency and conflicts

Batch append is idempotent at the logical-entry level. A retry after a lost response returns the
already-persisted contiguous entries with HTTP 200 when every supplied idempotency key, sequence,
and normalized payload exactly matches durable state.

The backend returns HTTP 201 when it inserts the batch for the first time.

It returns HTTP 409 with current next_sequence and a stable error code when:

- the requested starting sequence does not match;
- an idempotency key exists with a different payload;
- existing entries do not form the exact contiguous replay of the submitted batch;
- another writer advanced the session.

A mixed partial replay is not accepted initially. Atomic insertion means the batch endpoint should
not produce one, and rejection makes legacy-writer or migration conflicts explicit.

Lease failures retain current stable error codes. Oversized batches return HTTP 413. Invalid
entries return HTTP 400 with indexed field errors.

### 4. Retain single append only for migration

The existing endpoint remains temporarily available:

~~~http
POST /api/v1/agent-sessions/{agent_session_uid}/entries/append/
~~~

It is the compatibility operation for older Astro revisions. New Astro Tau revisions use batch
append for normal persistence. Astro must not convert an ambiguous batch timeout into individual
append calls because the batch may already have committed.

Removal requires telemetry proving no supported runtime revision still calls the legacy endpoint.

### 5. Batch Astro's ordered persistence queue

BackendSessionStorage.append() continues to update the lease-owned in-memory cache immediately. It
enqueues logical entries in order without issuing one HTTP transaction per entry.

The persistence worker drains a queue snapshot into the largest allowed batch, bounded by count and
serialized bytes. Only one batch writer may run per session. Entries appended while a batch is in
flight remain queued for the next batch.

Initialization entries are sent as one batch. A completed message and its leaf are transported
together whenever both are available.

Astro advances its durable sequence only from the acknowledged batch response. On conflict it
reloads durable entries, classifies exact replay versus divergence, and either recovers or marks
persistence failed. It never silently drops a queued entry.

### 6. Split output completion from durability completion

A streamed stateful turn has two milestones:

1. **Output complete:** Tau stopped model/tool execution and produced the final assistant result.
2. **Durable:** Every entry produced by the turn is committed by Django.

Astro no longer labels the entire interval as model thinking.

For assistant-ui streaming:

- emit assistant-ui finish immediately at output completion;
- continue the ordered persistence flush independently;
- emit [DONE] and close the stream after durability succeeds;
- if durability fails while connected, emit a persistence-specific error and terminate; and
- emit no model text or tool activity after finish.

The frontend stops its thinking/executing indicator on finish. It may show a separate
"saving conversation" state until [DONE], but persistence is not model execution.

The runtime remains the sole writer while persistence is pending. A subsequent same-session turn
may use the same in-memory ordered cache, but it cannot bypass a known persistence failure. A failed
batch makes that runtime unavailable for new turns until recovery or eviction.

For non-streaming stateful APIs, including direct A2A message, Astro continues to wait for
durability before returning because those contracts have no second streaming milestone.
Sessionless responses remain unaffected and create no session history.

Request cancellation after output completion must not cancel a shielded durability flush. Total
process loss may still lose entries that existed only in memory; this remains an acknowledged
at-least-once and idempotent-recovery tradeoff.

### 7. Separate lease ownership from active work

tdag-django and Astro distinguish a loaded runtime from an executing turn.

Persisted lease activity states are:

- loading;
- idle;
- working;
- persisting.

`cancel_requested` remains a separate cancellation overlay on the lease. `stale` remains a computed
runtime-state projection when a running session has no live `runtime_run` lease. Neither value is
stored in the activity column.

A valid lease means one runtime owns the session. It does not mean working.

Astro publishes lease-authenticated transitions at runtime load, turn start, output completion,
durability completion, and turn failure. Django stores activity, active turn UID, update timestamp,
and a monotonic activity revision in Tau-only runtime state linked one-to-one to the live lease.
Lease renewal extends ownership without changing activity or the activity revision.

AgentSession.status may continue to describe durable session lifecycle, but UI clients use
canonical runtime activity for spinners and cancellation. An idle retained runtime reports
working: false.

### 8. Permit parallel read-only MCP operations

Astro maps MCP tools advertised as both read-only and idempotent to Tau's parallel execution mode.
Mutating, unknown, or explicitly sequential tools remain sequential.

This prevents independent discovery calls from paying one backend round trip after another.
Parallelization does not relax environment scoping, authorization, or rate limits.

### 9. Preserve enriched telemetry and make it queryable by AgentSession

The enriched observability contract from tdag-django ADR 42 is already the source of truth. This ADR
does not introduce a second log envelope or replace structured events with persistence-specific
plaintext. Batch persistence and two-phase settlement extend that contract.

The canonical Environment contract in tdag-django has been renamed. The trusted log field is now
`organization_environment_uid`, supplied to Astro as
`MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID` or the trusted outer-boundary
`X-Organization-Environment-UID` header. Astro must not emit or accept the superseded
`organization_project_environment_uid` variants. This rename does not affect the independent,
exact per-conversation field `agent_session_uid`.

The current code was verified to provide the producer and storage foundations:

- Astro binds `agent_session_uid`, `agent_run_uid`, and `turn_uid` around Tau turns and propagates
  request, trace, actor, environment, service, revision, and runtime-instance context through
  structlog context variables;
- detached Astro work retains `agent_session_uid`, trace and environment identity plus an
  `operation_uid` and `origin_request_id`;
- tdag-django normalizes `agent_session_uid` as a canonical log attribute;
- Azure has a typed `AgentSessionUid` projection and falls back to
  `JsonPayload.agent_session_uid`; and
- the generic GCP and Azure provider query builders can already push down an
  `agent_session_uid` filter.

The verification found that tdag-django ADR-045 now closes the read-path gap through one
owner-observability source of truth. The canonical AgentSession action is
`GET /api/v1/agent-sessions/{agent_session_uid}/logs/`. It requires the canonical
`organization_environment_uid` as an active-Environment assertion, authorizes that value,
and verifies it equals the Environment derived from the visible session Agent. The backend fixes
`agent_uid`, `coding_agent_service_uid`, `user_uid`, and
`agent_session_uid` from the authorized owner. Callers cannot override those trusted
filters. Both provider wrappers push the complete typed filter object down before ordering,
limiting, and pagination; client-side filtering after pagination is not an acceptable substitute.

Every Astro batch dependency event uses the existing `dependency.call.completed` or
`dependency.call.failed` vocabulary and preserves these canonical top-level fields when available:

- `event_id`, `request_id`, `trace_id`, `span_id`, and `parent_span_id`;
- `operation_uid`, `causation_event_id`, and `origin_request_id` for detached persistence;
- `organization_uid`, `project_uid`, and `organization_environment_uid`;
- `coding_agent_service_uid`, `agent_uid`, and `agent_session_uid`;
- `agent_run_uid`, `turn_uid`, `runtime_revision`, `code_revision`, and
  `runtime_instance_uid`; and
- canonical dependency status, duration, attempt, outcome, retryability, and error fields.

Bounded batch extensions record entry count, canonical serialized bytes, expected and resulting
sequence, created versus replayed outcome, and persistence phase. Entry payloads, prompts, message
text, tool arguments/results, credentials, and lease tokens remain excluded.

Persistence created for a turn remains filterable by that turn's `agent_session_uid`,
`agent_run_uid`, and `turn_uid` even after the HTTP request ends. It uses `origin_request_id` rather
than pretending the expired request is still active. Lease renewal is not assigned a turn UID; it
retains session and runtime identity only.

Astro emits one canonical dependency event per backend attempt. Any suppression of duplicate raw
httpx success output must target the library logger and must not remove or reduce the enriched
canonical event. Model metrics count only provider requests, not generic message events. Turn token
totals sum provider calls while preserving cached-input and reasoning-token fields. A 201 entry
response is named as entry creation, never session creation.

### 10. Profile backend latency independently

Batching removes request-count amplification but does not make a slow backend acceptable.
tdag-django measures:

- authentication and runtime-context resolution;
- session and lease lock wait;
- lease validation;
- entry-schema validation;
- sequence and idempotency queries;
- database insertion; and
- response serialization.

Development tunnel and production service latency are reported separately. Production runtimes use
the canonical internal or production backend route, not a development ngrok endpoint.

## Required tdag-django Changes

This section is an implementation contract against the current backend, not a list of possible
approaches. Names below follow the existing `timeseries_orm/agents`, `common`, and
`tdag/pod_manager` layouts.

The persistence and runtime-activity implementation in this ADR is Tau-only. It must not add
methods, state, branches, migrations, or behavior to `PiHarnessBackend`, Pi checkpoint
services, or Astro Pi. Shared persistence/activity actions reject a non-Tau session before
invoking any Tau service. The AgentSession log action is the separately accepted,
harness-neutral ADR-045 observability contract and is reused rather than reimplemented.
Existing Pi persistence requests and serialized runtime-state responses remain unchanged.

### Backend file/change matrix

| Backend path | Required change |
| --- | --- |
| `models/harnesses.py` | Add the four-value `AgentRuntimeActivity` enum. |
| `models/agent_sessions.py` | Add Tau-only `TauAgentSessionRuntimeState`; leave `AgentSessionLease` and `TauAgentSessionEntry` unchanged. |
| `models/__init__.py` | Export `AgentRuntimeActivity` and `TauAgentSessionRuntimeState`. |
| `migrations/0016_tau_agent_session_runtime_state.py` | Create and backfill only the Tau runtime-state table; no lease-column or Tau-entry migration. |
| `serializers.py` | Add batch serializers, Tau activity serializers, and additive Tau runtime-state response fields; reuse ADR-045 owner-log serializers. |
| `services/tau_entries.py` | Add one atomic `append_tau_entries()` operation and refactor shared single/batch validation helpers. |
| `services/tau_runtime.py` | Create/reset Tau activity after Tau lease acquire, apply revisioned transitions, gate Tau cancellation, and project Tau runtime state. |
| `harness_backends/tau.py` | Dispatch batch append and Tau-only activity/state behavior around the existing shared lease primitives. |
| `views.py` | Add Tau-guarded `entries/append-batch` and `tau-runtime-activity` actions; reuse the canonical harness-neutral AgentSession `logs` action from ADR-045. |
| `timeseries_orm/settings_base/base.py` | Add batch-count and aggregate-byte settings. |
| `common/gcp/big_query/interface.py` | Accept the shared typed filter mapping while retaining canonical Environment ownership. |
| `common/azure/log_analytics.py` | Accept the shared typed filter mapping and project exact session equality through typed/JSON fields. |
| `tdag/pod_manager/services/runtime/log_store.py` | Accept one trusted typed filter mapping, prevent Environment override, push every filter to the provider, and defensively verify returned rows. |
| `tdag/pod_manager/services/runtime/owner_observability.py` | Authorize the explicit canonical Environment selector, derive trusted owner/session filters, and return the bounded sanitized ADR-045 envelope. |
| `admin.py` | Expose Tau runtime activity as read-only operational state without changing Pi lease admin behavior. |
| `docs/agents/sessions.md` | Document batch, activity, AgentSession-log filtering, limits, errors, and rollout. |
| `tests/test_multi_harness.py` | Add route, harness-dispatch, atomicity, replay, conflict, and limit coverage. |
| `tests/test_sessions.py` | Add lease-activity, runtime-state, cancellation, and transition coverage. |
| `tests/test_serializers.py` and `tests/test_agent_api.py` | Add serializer and generated-OpenAPI assertions. |
| `tdag/pod_manager/tests/test_runtime_logs.py` | Prove authorized provider-side filtering by `agent_session_uid` on GCP and Azure. |

### Existing backend behavior to preserve

The current write path is:

~~~text
AgentSessionViewSet.append_entry()
  -> TauAgentSessionEntryAppendRequestSerializer
  -> get_harness_backend(session).append_entry()
  -> TauHarnessBackend.append_entry()
  -> append_tau_entry()
  -> one TauAgentSessionEntry.objects.create()
~~~

`append_tau_entry()` already locks `AgentSession` and `AgentSessionLease`, validates the lease and
Tau schema, calculates the next sequence, and relies on two existing database constraints:

- `uniq_tau_session_entry_sequence` on `(agent_session, sequence)`; and
- `uniq_tau_session_entry_idempotency` on `(agent_session, idempotency_key)`.

Those constraints and the `TauAgentSessionEntry` columns remain unchanged. Batch history therefore
requires no entry-table migration. The legacy `append_tau_entry()` service and
`entries/append/` action remain behaviorally unchanged during migration.

### Serializer contract

In `timeseries_orm/agents/serializers.py`, add:

~~~python
class TauAgentSessionEntryBatchItemSerializer(serializers.Serializer):
    idempotency_key = serializers.CharField(max_length=255, allow_blank=False)
    entry = TauSessionEntryJSONField()


class TauAgentSessionEntryBatchAppendRequestSerializer(serializers.Serializer):
    lease_token = serializers.UUIDField()
    expected_sequence = serializers.IntegerField(min_value=0)
    entries = TauAgentSessionEntryBatchItemSerializer(many=True, allow_empty=False)


class TauAgentSessionEntryBatchAppendResponseSerializer(serializers.Serializer):
    entries = TauAgentSessionEntrySerializer(many=True)
    next_sequence = serializers.IntegerField(min_value=0)
    created_count = serializers.IntegerField(min_value=0)
    replayed = serializers.BooleanField()
~~~

DRF handles malformed envelopes such as missing fields and non-list `entries` with its normal HTTP
400 validation response. The service handles entry-schema and batch-semantic failures so they keep
the established `AgentSessionCheckpointErrorResponseSerializer` shape and stable `error_code`.
Indexed entry failures use `field_errors.entries.<zero-based-index>`.

### ViewSet and harness dispatch

In `timeseries_orm/agents/views.py`, add this action to `AgentSessionViewSet`:

~~~python
@action(detail=True, methods=["post"], url_path="entries/append-batch")
def append_entries_batch(self, request, uid=None, pk=None): ...
~~~

The action must:

1. resolve and authorize the session with the existing `self.get_object()` path;
2. reject `session.harness != tau` with the standard `runtime_operation_unsupported` response,
   before harness dispatch;
3. validate with `TauAgentSessionEntryBatchAppendRequestSerializer`;
4. call `TauHarnessBackend.append_entries(...)` exactly once through the resolved backend;
5. serialize success with `TauAgentSessionEntryBatchAppendResponseSerializer`;
6. serialize service failure with `AgentSessionCheckpointErrorResponseSerializer`; and
7. document 200, 201, 400, 409, and 413 responses using `extend_schema`.

In `timeseries_orm/agents/harness_backends/tau.py`, add `append_entries()` and dispatch it to
`append_tau_entries()`. Do not edit `harness_backends/pi.py`; the ViewSet's Tau guard means the Pi
backend never receives this operation.

The URI remains nested under the canonical `AgentSession` resource, but the action is explicitly
Tau-only. It creates only ordered `TauAgentSessionEntry` rows inside the addressed session. It
creates no `AgentTask`, checkpoint, additional `AgentSession`, insight row, or transcript
projection.

### Transaction algorithm in `services/tau_entries.py`

Add `append_tau_entries()`; do not implement it by calling `append_tau_entry()` in a loop. Refactor
shared JSON sizing, lease validation, and entry validation into private helpers used by both
services, while preserving every existing single-append error code.

The batch service executes the following algorithm:

1. Before acquiring database locks, reject an empty batch, too many entries, an oversized entry,
   an oversized aggregate payload, duplicate request idempotency keys, and non-JSON values.
2. Validate every `entry.id == idempotency_key` and run `validate_tau_session_entry()` for every
   item. Collect all item failures by zero-based index and insert nothing if any item is invalid.
3. Enter one `transaction.atomic()` block.
4. Lock `AgentSession.all_objects.select_for_update().get(pk=session.pk)` and then its
   `AgentSessionLease` in the same order used by `append_tau_entry()`.
5. Require `harness=tau`, a non-expired lease, matching `lease_token`, and
   `lease_purpose=runtime_run`. A pending cancellation does not reject durability writes; an
   aborted turn must still be persistable.
6. Fetch all rows for the submitted idempotency keys in one query and calculate the durable next
   sequence while the session lock is held.
7. If every key already exists, accept only an exact replay: each row must occupy
   `expected_sequence + item_index`, and its stored normalized `entry_json` must equal the submitted
   normalized entry. Return HTTP 200, `created_count=0`, `replayed=true`, and the current durable
   `next_sequence`.
8. If only some keys exist, reject the whole request. If an existing key has a different payload or
   sequence, reject it. Never insert the missing suffix of a partially replayed request.
9. If no key exists, require `expected_sequence` to equal the durable next sequence, assign
   contiguous sequences, and call `TauAgentSessionEntry.objects.bulk_create(rows)` once.
10. Serialize the created rows in request order and return HTTP 201, `created_count=len(entries)`,
    `replayed=false`, and the sequence immediately after the batch.

The session row lock is the cross-endpoint serialization mechanism: legacy single append and batch
append acquire the same lock, so their sequence allocations cannot interleave. `bulk_create()` must
not use `ignore_conflicts`; the database constraints remain the final integrity guard. An
unexpected `IntegrityError` is logged and converted to a stable conflict response after rollback,
never exposed as HTTP 500.

The batch service does not validate parent or leaf references beyond the existing Tau JSON schema.
This deliberately keeps batch append equivalent to submitting the same ordered entries through the
legacy endpoint.

### Exact status and error contract

| Condition | HTTP | `error_code` or result |
| --- | ---: | --- |
| First complete insert | 201 | `created_count > 0`, `replayed=false` |
| Exact complete replay | 200 | `created_count=0`, `replayed=true` |
| Invalid Tau item, duplicate request key, or ID/key mismatch | 400 | `runtime_entry_invalid` with indexed `field_errors` |
| No lease, expired lease, token mismatch, or wrong lease purpose | 409 | Existing `checkpoint_lease_*` code |
| Stale `expected_sequence` or concurrent sequence advance | 409 | `runtime_sequence_conflict` with `next_sequence` |
| Existing key has a different payload or sequence | 409 | `runtime_idempotency_conflict` with `next_sequence` |
| Only part of the submitted batch already exists | 409 | `runtime_entry_batch_partial_replay` with `next_sequence` |
| Any non-Tau session | 409 | `runtime_operation_unsupported` from the action guard; no non-Tau backend call |
| One entry exceeds `AGENT_RUNTIME_ENTRY_MAX_BYTES` | 413 | Existing `runtime_entry_too_large` |
| Batch exceeds count limit | 413 | `runtime_entry_batch_too_many` |
| Batch exceeds aggregate byte limit | 413 | `runtime_entry_batch_too_large` |

All service-generated errors include `agent_session_uid`. Conflict responses include the current
durable `next_sequence`. No failure response may contain a successful subset.

### Settings

In `timeseries_orm/timeseries_orm/settings_base/base.py`, retain
`AGENT_RUNTIME_ENTRY_MAX_BYTES` and add:

~~~python
AGENT_RUNTIME_ENTRY_BATCH_MAX_ENTRIES = int(
    os.getenv("AGENT_RUNTIME_ENTRY_BATCH_MAX_ENTRIES", "100")
)
AGENT_RUNTIME_ENTRY_BATCH_MAX_BYTES = int(
    os.getenv("AGENT_RUNTIME_ENTRY_BATCH_MAX_BYTES", str(8 * 1024 * 1024))
)
~~~

Both settings must be positive at startup. Astro may submit smaller batches but must never assume a
larger backend limit.

### Persisted runtime activity migration

Batch append fixes transport amplification; it does not fix the backend's false `working=true`
projection. The current `services/session_runtime.py` computes `working` solely from
`AgentSession.status=running` plus a valid `runtime_run` lease, while `services/session_leases.py`
renews that lease for an idle warm runtime.

Add `AgentRuntimeActivity` in `models/harnesses.py` with exactly `loading`, `idle`, `working`, and
`persisting`. Export it from `models/__init__.py`. Add a Tau-only child model in
`models/agent_sessions.py`:

~~~python
class TauAgentSessionRuntimeState(
    DerivedOrganizationEnvironmentMixin,
    models.Model,
):
    ORGANIZATION_ENVIRONMENT_OWNER_PATH = "agent_session_lease__agent_session"

    agent_session_lease = models.OneToOneField(
        AgentSessionLease,
        primary_key=True,
        related_name="tau_runtime_state",
        on_delete=models.CASCADE,
    )
    runtime_activity = models.CharField(
        max_length=16,
        choices=AgentRuntimeActivity.choices,
        default=AgentRuntimeActivity.IDLE,
    )
    active_turn_uid = models.UUIDField(null=True, blank=True)
    activity_revision = models.PositiveBigIntegerField(default=0)
    activity_updated_at = models.DateTimeField(default=timezone.now)
~~~

Create `0016_tau_agent_session_runtime_state.py` after the prior agents migration head `0015`. Its
data migration creates an `idle` row only for an
existing lease whose `agent_session.harness` is Tau. It creates no row for Pi. `AgentSessionLease`
receives no new columns, and Pi checkpoint/lease tables remain unchanged.

### Lease-authenticated activity updates

Add the explicitly Tau-only route:

~~~http
PATCH /api/v1/agent-sessions/{agent_session_uid}/tau-runtime-activity/
~~~

The action uses `TauAgentSessionRuntimeActivityPatchSerializer`:

~~~json
{
  "holder_id": "pod/astro-tau-stream-abc",
  "lease_token": "6a338b15-6e8c-4fc9-b04d-c67f0fa3d862",
  "expected_activity_revision": 7,
  "runtime_activity": "working",
  "active_turn_uid": "c51489b9-946f-4627-981e-1d30e9c9ba41"
}
~~~

Every field shown is required except `active_turn_uid`, which is conditionally nullable. The action
calls `self.get_object()`, rejects a non-Tau session before dispatch, and then calls the Tau backend.
The Tau service locks session, lease, and Tau runtime-state row in that order, reuses active lease
validation, requires `runtime_run`, compares the revision, and updates activity atomically.

The existing `PATCH .../runtime-state/` provider/model/thinking contract remains unchanged for all
harnesses. Runtime activity is not added to that shared mutation serializer.

Allowed transitions are:

| From | To |
| --- | --- |
| `loading` | `idle`, `working` |
| `idle` | `working` |
| `working` | `persisting`, `idle` |
| `persisting` | `idle` |

An exact same-state retry with the same turn UID is idempotent and does not increment the revision.
Every real transition increments `activity_revision` once and sets `activity_updated_at` to backend
time. `working` and `persisting` require `active_turn_uid`; `idle` requires it to be null. A revision
mismatch returns `409 runtime_activity_revision_conflict`; an invalid transition returns
`409 runtime_activity_transition_invalid`; an invalid turn/activity pairing returns
`400 runtime_activity_turn_invalid`.

After `TauHarnessBackend.acquire_lease()` successfully calls the existing shared lease service, it
creates or resets the Tau child row to `loading`, null turn UID, revision zero, and backend time.
After `TauHarnessBackend.renew_lease()` calls the existing shared renew service, it reloads and
projects the child row without modifying it. Shared lease acquire/renew code and
`PiHarnessBackend` are not changed. Lease release deletes the child row through cascade.

For Tau, `services/tau_runtime.py` projects:

- a terminal `AgentSession.status` when the durable session is terminal;
- otherwise the persisted activity when a live `runtime_run` lease exists;
- otherwise `stale` when the durable session status is `running`; and
- `working=true` only for a live lease whose persisted activity is exactly `working`.

The response adds `runtime_activity`, `active_turn_uid`, `activity_revision`, and
`activity_updated_at`. `cancel_requested` and `cancel_state` remain separate. A cancellation request
is accepted only when the live Tau lease activity is `working`; `idle` and `persisting` return
`cancel_state=not_running` without setting cancellation fields. Transitioning from `idle` to a new
`working` turn clears cancellation fields from the prior turn.

`TauHarnessBackend.get_runtime_state()` and `request_cancel()` use these Tau services. No Pi file,
service, model row, route behavior, runtime-state projection, checkpoint behavior, or serialized Pi
response changes under this ADR.

### Backend documentation, schema, administration, and metrics

The same tdag-django change updates:

- `docs/agents/sessions.md` with both wire contracts, examples, limits, errors, and migration rules;
- `AgentSessionViewSet` `extend_schema` declarations and the generated OpenAPI schema;
- `timeseries_orm/agents/admin.py` so the Tau child activity, turn UID, revision, and update time are
  visible and read-only without changing Pi lease administration; and
- route/schema assertions in `tests/test_multi_harness.py` and `tests/test_agent_api.py`.

Emit one structured metric/log event per batch with session UID, expected and resulting sequence,
entry count, canonical bytes, created/replayed/conflict outcome, lock-wait duration, validation
duration, insert duration, total service duration, and stable error code. Do not log entry payloads,
message text, tool results, or lease tokens.

### AgentSession-filtered runtime logs

ADR-048 does not add a Tau-specific log route. It reuses the canonical owner-scoped action
implemented by tdag-django ADR-045:

~~~http
GET /api/v1/agent-sessions/{agent_session_uid}/logs/
    ?organization_environment_uid=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
    &start=1787806800
    &end=1787810400
    &limit=100
~~~

The URL session is resolved through normal AgentSession object authorization. The required
`organization_environment_uid` query parameter is the platform-wide active-Environment
assertion. Django resolves it through `visible_environment_for_request()` and requires it to
match the Environment derived from `session.agent`. Supplying the UID cannot grant access
or redirect the query to another Environment.

After authorization, the server constructs the trusted provider filter mapping from the owner:

~~~text
organization_environment_uid = <authorized and owner-matched Environment UID>
agent_uid                    = <session Agent UID>
coding_agent_service_uid     = <session Agent service UID>
user_uid                     = <session creator UID>
agent_session_uid            = <authorized URL AgentSession UID>
~~~

The caller may not replace any of these fields. On the Agent-level log action only, an optional
`agent_session_uid` query parameter is accepted after Django proves that the session belongs
to both the addressed Agent and the authorized user.

`read_owner_logs()` sends the complete trusted mapping to
`fetch_runtime_environment_logs(filters=...)`. That service protects the canonical
Environment scope, sends the same typed mapping to either provider wrapper, and defensively rejects
any returned row that does not carry exact Environment and trusted-filter attribution. BigQuery
and Azure apply all supported predicates before ordering and limiting. Azure uses its typed
`AgentSessionUid` projection with the canonical `JsonPayload.agent_session_uid`
fallback; GCP uses `jsonPayload.agent_session_uid`.

The response is the shared ADR-045 owner envelope, not a persistence-specific shape:

~~~json
{
  "organization_environment_uid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "start": 1787806800,
  "end": 1787810400,
  "next_cursor": null,
  "truncated": false,
  "rows": []
}
~~~

Time defaults to a one-hour lookback, is capped at seven days, and accepts epoch seconds or
milliseconds. Page size defaults to 100 and is capped at 500. Continuation uses an opaque signed
cursor bound to Environment, owner, trusted filters, time window, public filters, and page size.
Public filters are limited to controlled severity, request ID, event, and outcome values. Provider
query language and infrastructure identifiers are never accepted.

The route returns sanitized ADR-042 fields only. Rows without exact `agent_session_uid` or
other trusted attribution are excluded rather than inferred. Provider unavailability returns the
shared stable owner-observability 503 response. The action creates no session, task, transcript,
checkpoint, entry, or runtime activity and does not contact Astro.

This owner-observability action is harness-neutral. Reusing it does not add any Pi persistence,
runtime-state, migration, producer, or harness-backend behavior. The Tau-specific work in ADR-048
is limited to producing correct enriched correlation and to the batch/activity contracts above.

## Required Astro Tau Changes

Astro requires:

1. typed batch request and response models;
2. a batch route helper and backend-client operation;
3. queue draining by count and aggregate bytes;
4. exact replay and conflict recovery;
5. two-phase output-complete and durable settlement;
6. shielded persistence after disconnect following output completion;
7. runtime activity publication at each lifecycle transition;
8. failure gating after a persistence error;
9. read-only and idempotent MCP parallelization;
10. corrected model, tool, token, and dependency accounting;
11. removal of duplicate raw success logs from the production machine sink without weakening the
    enriched canonical event;
12. preservation of `agent_session_uid`, `agent_run_uid`, and `turn_uid` on turn-owned background
    persistence, with `origin_request_id` after request detachment;
13. bounded batch extensions on the existing dependency events, never entry or message content.

## Frontend and SDK Impact

The batch endpoint is runtime-internal. mainsequence-sdk does not need a high-level batch-history
API unless it intentionally exposes low-level runtime persistence.

The session-filtered log action is user-facing. mainsequence-sdk must expose the canonical
ADR-045 owner-log operation for `agent_session_uid` and the required
`organization_environment_uid` active-Environment assertion, plus bounded time, cursor,
limit, severity, request ID, event, and outcome fields. It must not expose provider or
infrastructure filters. The frontend uses the capability URL returned by AgentSession
`observability.application_logs_url` and never downloads an Environment-wide page to filter
locally.

Assistant-ui consumers treat finish as output completion and [DONE] as stream and durability
completion. Clients that keep a thinking spinner until socket closure or [DONE] must change.

Runtime-state consumers stop interpreting AgentSession.status equal to running as proof of active
model execution. They use working and canonical activity.

## Failure and Recovery Semantics

- Invalid batches create no rows.
- A connection lost after commit is recovered by exact replay.
- An expired lease rejects the batch and stops that runtime from accepting turns.
- Persistence failure before output completion uses existing streamed error behavior.
- Persistence failure after output completion emits a persistence error when possible, marks the
  runtime unhealthy, rejects new turns, and evicts without releasing an incompletely flushed lease.
- A replacement runtime reloads only durable entries and uses entry IDs plus expected sequence to
  classify an ambiguous prior batch.
- Astro never silently translates an ambiguous batch into single appends.

## Migration Plan

1. Deploy tdag-django first with the Tau-only runtime-state table migration, batch/activity
   actions, ADR-045 owner-scoped AgentSession logs, provider-side typed filtering, OpenAPI, tests,
   and metrics. Keep single append
   operational. Existing Astro revisions and all Pi behavior continue unchanged.
2. Deploy Astro Tau with the batch client and activity writer. The new revision requires the batch
   endpoint; HTTP 404 is a deployment-order error and must fail visibly. It must not fall back to
   individual appends after any batch attempt, because an ambiguous request may have committed.
3. On lease acquisition, publish `loading`, hydrate Tau, then publish `idle`. For every turn publish
   `working`, then `persisting` at output completion, then `idle` only after batch durability.
4. Initially retain the existing stream settlement boundary while verifying a tool-heavy local turn
   produces byte-for-byte equivalent Tau history after reload with bounded backend calls.
5. Introduce two-phase streamed settlement and update frontend spinner and persistence state after
   batch durability and activity telemetry are stable.
6. Enable parallel execution only for MCP tools annotated read-only and idempotent.
7. Observe batch size, flush latency, replay and conflict rate, persistence failures, activity
   revision conflicts, and legacy append traffic in development and production.
8. Remove single append only after telemetry proves all supported runtime revisions use batch
   append. Its later removal requires a separate compatibility decision.

## Verification Requirements

### tdag-django

Serializer and route tests prove:

- the batch serializers expose the exact fields and reject empty or malformed envelopes;
- `/entries/append-batch/` resolves to `append_entries_batch` and remains under canonical
  `AgentSessionViewSet` authorization;
- `/tau-runtime-activity/` rejects non-Tau sessions before backend dispatch;
- `/logs/` resolves to the shared owner-observability action, requires
  `organization_environment_uid`, and fixes the authorized Agent, service, actor, and
  AgentSession predicates server-side;
- the retired duplicate `/runtime-logs/` route and its bespoke serializers are absent;
- the generated OpenAPI document contains the request, 200/201 response, and documented errors;
  and
- the existing `/entries/append/` schema and route remain unchanged.

Database service tests prove:

- a batch inserts all entries with contiguous sequences using one `bulk_create()` call;
- one invalid item produces indexed errors and inserts zero rows;
- exact retry returns HTTP 200 without duplicate rows;
- changed payload or changed sequence under an existing idempotency key returns HTTP 409;
- a partial replay returns `runtime_entry_batch_partial_replay` and inserts zero rows;
- stale starting sequence reports the current `next_sequence`;
- single append racing batch append cannot interleave sequences;
- session and lease locks are acquired once per batch, not once per item;
- lease missing, expiration, token, purpose, harness, count, per-entry byte, and aggregate-byte
  failures return the specified stable errors;
- a pending cancellation does not prevent a valid durability batch;
- a non-Tau request is rejected by the shared action without calling a non-Tau harness backend; and
- a database exception rolls back the complete batch and is projected as a stable conflict.

Lease and runtime-state tests prove:

- migration backfill creates idle state only for an existing Tau lease and creates no non-Tau row;
- Tau runtime-run acquire returns loading at revision zero;
- renew changes heartbeat and expiration but not activity, turn UID, or revision;
- valid transitions increment the revision exactly once and same-state retries are idempotent;
- stale revisions, illegal transitions, and invalid turn/activity pairs return their specified
  errors;
- idle and persisting project `working=false`, while working projects `working=true`;
- cancellation is accepted only for working and remains separate from activity; and
- the Tau changes do not alter existing non-Tau serialized runtime-state output.

Owner-observability runtime-log tests prove:

- the AgentSession must be visible to the authenticated user and its owner-derived Environment must
  equal the required authorized `organization_environment_uid` assertion;
- the session action sends server-fixed Agent, service, actor, and exact AgentSession predicates;
- a caller cannot override the path AgentSession or any trusted owner filter;
- GCP SQL contains exact Environment and `jsonPayload.agent_session_uid` predicates before
  limit;
- Azure KQL contains exact Environment and typed/JSON AgentSession predicates before `take`;
- provider pagination and the signed cursor are calculated over the already-filtered result set;
- a defensive verification pass rejects rows for another Environment, Agent, service, user, or
  session;
- detached persistence rows remain discoverable by the originating `agent_session_uid`; and
- sessionless and pre-enrichment rows are excluded rather than guessed.

The focused backend verification command is:

~~~shell
.venv/bin/python timeseries_orm/manage.py test \
  agents.tests.test_multi_harness \
  tdag.pod_manager.tests.test_runtime_logs \
  --keepdb --noinput
~~~

This focused command is the ADR acceptance gate. The repository's broader test suites remain
separate regression gates; unrelated failures in them must not be mistaken for failures of this
contract.

The concurrent-writer case must run against PostgreSQL because SQLite does not exercise the
production `select_for_update()` behavior.

### Astro Tau

- initialization entries are sent in one batch;
- completed message and leaf are transported together;
- tool-heavy turns use bounded batch calls instead of one call per entry;
- entries appended during an in-flight batch remain ordered in the next batch;
- exact replay recovers a lost response;
- conflict and persistence errors gate subsequent turns;
- finish appears at output completion before a deliberately blocked flush;
- [DONE] appears only after durability;
- disconnect after finish does not cancel persistence;
- non-streaming stateful responses still wait for durability;
- sessionless responses perform no persistence;
- idle runtime activity is not reported as working;
- read-only MCP calls can run concurrently while mutations remain sequential;
- telemetry counts actual provider calls and sums token usage;
- session-bound batch and durability events carry canonical `agent_session_uid`;
- turn-owned detached persistence retains agent run and turn correlation plus
  `origin_request_id`; and
- the stored-log endpoint returns those events when filtered by the exact AgentSession UID.

### End-to-end performance

A latency-injected integration test asserts request-count invariants rather than fragile timing:

- no AgentSession is created during entry persistence;
- one session is reused across turns and runtime reloads;
- batch-call count is bounded by configured limits, not logical entry count;
- a representative tool turn does not append every message and leaf separately; and
- output completion is visible before the durability flush is released.

An opt-in real-provider acceptance test builds and starts Astro with Docker Compose, verifies the
local response belongs to that container, executes a turn, restarts the container, and verifies a
second turn recalls Django-persisted conversation state. It records response headers, first SSE
event, time to first text, maximum SSE gap, output streaming, finish, durability, total-turn,
restart, and resumed-turn timings in a machine-readable report. Configurable hard budgets fail the
test when a structurally correct conversation still provides unacceptable UX.

Production acceptance also requires latency percentiles for runtime load, MCP calls, batch append,
output completion, and durability completion.

## Consequences

### Positive

- Remote persistence changes from O(entries) HTTP transactions to O(batches).
- Tau retains canonical append-only history and resume behavior.
- Persistence time is no longer labeled as model thinking.
- Idle lease ownership no longer appears as active work.
- Backend transaction and idempotency behavior remain explicit.
- Logs distinguish sessions, entries, provider calls, tools, and lease maintenance.

### Costs and risks

- The endpoint and two-phase stream lifecycle require coordinated cross-repository deployment.
- More not-yet-durable entries may exist in memory.
- Output can be visible briefly before its history is committed.
- Frontends equating socket closure with model completion require adjustment.
- Batch limits must prevent long transactions and oversized requests.
- Mixed deployments retain the legacy endpoint until old revisions disappear.

## Alternatives Rejected

### Keep one request per entry and optimize only Django

Even a fast endpoint is amplified by message and leaf pairs and tool loops, retaining avoidable
authentication, serialization, network, and transaction overhead.

### Remove durable backend persistence

Django is the canonical cross-replica store required for resume, ownership, auditability, and
runtime replacement.

### Remove all leaf entries immediately

Leaf entries are part of Tau's branch and resume semantics. Batching solves the urgent UX problem
without redefining history.

### Emit [DONE] before persistence and hide failures

That silently turns durable sessions into best-effort history. Output completion may be early, but
durability completion and failure remain observable.

### Retry an ambiguous batch as individual appends

The batch may already have committed. Splitting it makes atomicity and diagnosis harder.

### Treat a valid lease as active work

Astro intentionally retains leases while idle. Ownership and execution are different states.

## Relationship to Existing Decisions

This ADR amends ADR 45, **Nonblocking Tau Runtime I/O**. It retains the lease-owned in-memory cache,
single writer, and recovery behavior. It replaces:

- one remote request per queued logical entry with atomic batch append; and
- one combined output-and-durability settlement boundary with explicit output-complete and durable
  milestones.

A2A response-kind and sessionless-response decisions are unchanged. Stateful A2A messages use
durable sessions; sessionless responses create no session, task, checkpoint, or transcript.

This ADR also consumes, but does not supersede, tdag-django ADR 42, **End-to-end Runtime Request and
Agent Observability**. ADR 42 remains authoritative for the canonical log envelope, identity trust,
redaction, provider storage, and high-cardinality-field rules. ADR 48 requires Tau persistence to
preserve that envelope and closes the verified authorized-read gap for exact
`agent_session_uid` filtering.

## Non-Goals

- Replacing Tau's append-only session tree.
- Adding batch persistence, activity state, log actions, or observability behavior to Pi or Astro
  Pi.
- Changing A2A response-kind selection.
- Persisting sessionless response traffic.
- Introducing a task broker or persistence sidecar.
- Guaranteeing recovery of entries never committed before total process loss.
- Treating development ngrok measurements as production latency targets.
