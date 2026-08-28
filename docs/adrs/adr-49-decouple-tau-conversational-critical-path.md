# ADR 49: Decouple the Tau Conversational Critical Path

Status: Accepted
Date: 2026-08-27
Implementation Status: Implemented in Astro Tau and tdag-django; deployment percentile validation pending
Owners: Astro Tau and tdag-django

## Context

ADR 48 introduced atomic Tau entry batches, lease-owned runtime activity, and two-phase turn
settlement. It removed per-entry request amplification and preserved the useful contract that
assistant-ui finish means model output is complete while [DONE] means the output is durable.

The first real conversation test through local Docker Compose proved correctness but failed the UX
latency gates. It used the main Astro orchestrator AgentSession, restarted the local Astro Tau
container, and resumed the prior durable conversation from local tdag-django.

| Measurement | First cold turn | Resumed after restart |
| --- | ---: | ---: |
| Response headers | 0.009 s | 0.005 s |
| First SSE event | 17.764 s | 16.195 s |
| First text token | 17.764 s | 16.195 s |
| finish | 17.862 s | 17.012 s |
| finish to [DONE] | 3.437 s | 3.718 s |
| Total | 21.299 s | 20.731 s |

The generated response was correct, and the resumed turn recovered the exact token persisted by the
first turn. Container restart-to-ready was 3.844 seconds. The defect is platform latency, not
provider correctness, session durability, Docker execution, or model execution in Kubernetes.

Correlated Astro and Django logs identify the cold path:

~~~text
runtime credential exchange                       2.49 s
AgentSession lookup                               1.41 s
runtime lease acquisition                         2.12 s
capabilities and history loading                  1.58 s / 1.58 s
provider credential hydration                     1.90 s
MCP initialize/list tools/list resources          multiple requests
loading -> idle runtime activity PATCH            1.65 s
idle -> working runtime activity PATCH            1.81 s
provider execution and output                     approximately 4 s
working -> persisting activity PATCH              approximately 1.7 s
output append batch                               approximately 1.7 s
persisting -> idle activity PATCH                 approximately 1.8 s
~~~

The MCP catalog contained 88 tools and was approximately 230 KB. It was rediscovered for every
loaded session. Django's atomic batch service spent approximately 0.25 seconds in database work,
while the complete authenticated HTTP request took approximately 1.4 to 1.7 seconds.

Four architectural couplings cause the delay:

1. runtime credential exchange refreshes deployment state and queues coding-agent reconciliation;
2. cold session loading fans out across session, lease, capability, history, provider, and MCP
   operations;
3. remote runtime activity is treated as a serial barrier before provider execution; and
4. two remote activity transitions surround the one durability operation after model output.

The first coupling is incorrect. A runtime credential authenticates an already-running data-plane
caller. Authentication must not inspect or repair the Kubernetes deployment that launched it.
Kubernetes remains a deployment mechanism, not part of the conversation execution path.

Astro caches an exchanged runtime JWT until it approaches expiration, so the 2.49-second exchange is
primarily a cold-process and refresh cost. Django still validates the durable runtime credential and
target for every authenticated request, so request authorization remains material.

## Decision

The Tau conversational path will be reduced to one cold-session bootstrap, no blocking activity
operation before provider execution, and one durability/turn-commit operation after output.

~~~text
Astro startup
  -> exchange runtime credential without reconciliation
  -> initialize the shared Main Sequence MCP runtime/catalog
  -> report ready

Cold AgentSession
  -> POST tau-runtime/bootstrap for lease + compatible snapshot + entry delta
  -> restore the local Tau CodingSession
  -> start provider execution
  -> publish activity and input persistence without blocking the provider

Warm turn
  -> start provider execution immediately

Output settlement
  -> emit finish
  -> atomically persist remaining output and commit the turn idle
  -> emit [DONE]
  -> generate and upload the next resume snapshot asynchronously
~~~

### 1. Runtime credential exchange is authentication-only

KnativeRuntimeCredentialService.exchange() in tdag-django will perform only bounded local work:

1. resolve the exact credential by credential_id;
2. verify the supplied secret hash;
3. verify active/revoked state and credential rotation generation;
4. verify durable target, principal User, and Organization bindings;
5. issue a short-lived runtime JWT;
6. update last_used_at; and
7. emit a credential-exchange audit event.

Successful or failed exchange will not:

- call refresh_coding_agent_service_runtime_state();
- use is_runtime_routable() as an authentication prerequisite;
- enqueue coding-agent reconciliation;
- contact Kubernetes, Knative, Celery, Redis, a cloud API, or another HTTP service; or
- repair deployment state.

An already-running runtime remains authenticatable when the control-plane routability projection is
stale. Exchange still rejects an unknown credential, wrong secret, revoked/inactive credential,
stale rotation generation, or invalid target/principal/organization binding.

Deployment reconciliation remains backend-owned and is triggered by service/deployment mutations,
credential creation or rotation, health monitors, explicit deployment workflows, and periodic
reconciliation. Credential exchange is not a reconciliation trigger. Merely moving the existing
enqueue to an asynchronous helper is insufficient because broker publication and precondition
resolution can still block the request.

Astro RuntimeCredentialAuth will prefetch the token during process startup. /live may report process
liveness earlier, but /ready must wait for successful backend authentication. Refresh remains
single-flight and occurs before expiration.

### 2. Preserve immediate authorization while removing generic request work

This ADR does not weaken revocation semantics. Each runtime-authenticated request still validates
durable credential generation and target authorization.

tdag-django will replace request-time credential.full_clean() and generic target resolution with a
purpose-built authorization query/service loading only the credential, principal, organization,
target, status, and rotation fields required by runtime authorization. The resulting
AuthenticatedKnativeRuntimeContext is attached once and reused by permissions and object
authorization. No view or permission may independently reload the same credential or service.

The first implementation does not add an eventually consistent authorization cache. Any future
cache requires a separate decision defining its revocation bound and invalidation mechanism.

Django database connections become environment-configurable persistent connections. The initial Tau
configuration is:

~~~python
CONN_MAX_AGE = 60
CONN_HEALTH_CHECKS = True
~~~

Tests must prove connection reuse and safe recovery after a dead connection.

Before optimizing further, tdag-django will measure these non-secret phases:

- authentication;
- runtime authorization;
- database connection acquisition;
- permission and object lookup;
- request deserialization;
- domain service execution;
- response serialization; and
- total request handling.

Measurements are structured fields and a sanitized Server-Timing header in local/test environments.
Production may retain structured/OpenTelemetry measurements without exposing the header.

### 3. Add a canonical Tau runtime bootstrap operation

tdag-django adds:

~~~http
POST /api/v1/agent-sessions/{agent_session_uid}/tau-runtime/bootstrap/
~~~

Request:

~~~json
{
  "holder_id": "astro-holder-id",
  "ttl_seconds": 300,
  "bootstrap_request_uid": "c7deff96-f9b3-4d20-a7a6-cd4d85736546",
  "history_after_sequence": null,
  "known_capability_hashes": [],
  "supported_snapshot_schema_versions": [1],
  "tau_runtime_version": "..."
}
~~~

The operation is authorized against the exact runtime credential target and exact AgentSession. A
non-Tau session is rejected before lease acquisition.

The response contains:

- canonical AgentSession harness, provider, model, and thinking configuration;
- the acquired runtime_run lease;
- loading activity, server revision, and client activity sequence;
- the latest compatible Tau resume snapshot plus entries after its base sequence, or bounded full
  history with next-sequence pagination when no compatible snapshot exists;
- capability bindings plus uncached immutable capability bodies; and
- the existing model-provider hydration result for the exact session.

The bootstrap is an orchestration boundary, not duplicate domain logic. It calls the existing
canonical services for AgentSession resolution, runtime authorization, lease acquisition, Tau
history, capabilities, provider credential ownership/hydration, and Tau runtime-state initialization.

Provider credentials never appear in logs, traces, timing headers, exception bodies, or the
real-conversation report.

Lease acquisition and activity reset occur in a short transaction. Large history and capability
serialization must not hold the lease lock. bootstrap_request_uid is stored on the Tau child
runtime-state row and makes retries idempotent. Repeating the same request for the same session and
holder returns the same live lease; a different holder continues to receive the canonical conflict.

History remains bounded and paginated. A compacted ordinary session should fit in the first response.
Capability content uses existing content hashes; Astro sends cached hashes and Django omits those
immutable bodies.

### 4. Persist an Astro-produced Tau resume snapshot

Canonical Tau entries remain the source of truth, but raw entries are not the fastest cold-start
representation. Replaying a long append-only history, rebuilding the active tree/leaf, reapplying
compaction, and rematerializing runtime configuration repeats work that the prior Astro process
already completed.

After a successful durable turn commit, Astro produces a deterministic, versioned
TauResumeSnapshot. The snapshot is an opaque acceleration artifact from Django's perspective. Astro,
not Django, owns the Tau serialization semantics.

The snapshot contains only the state needed to restore the local Tau session:

- the compacted Tau session tree and current leaf;
- the durable next sequence immediately following the entries represented by the snapshot;
- compaction summary/state required by Tau;
- provider, model, and thinking identifiers, but no provider credential;
- the runtime configuration hash and capability-set hash;
- the Tau runtime version and snapshot schema version;
- the last committed turn UID; and
- a SHA-256 digest of the canonical snapshot payload.

Snapshots never contain provider credentials, runtime JWTs, runtime credential secrets, lease
tokens, open file handles, MCP transports, HTTP clients, or executable Python pickle data. The
serialization format is deterministic typed JSON owned by Astro/Tau. Conversation content inside the
snapshot receives the same authorization, retention, and log-redaction treatment as canonical
session entries.

tdag-django adds the Tau-only operation:

~~~http
PUT /api/v1/agent-sessions/{agent_session_uid}/tau-runtime/resume-snapshot/
~~~

Request:

~~~json
{
  "holder_id": "astro-holder-id",
  "lease_token": "...",
  "base_sequence": 81,
  "last_committed_turn_uid": "b1889848-0034-4f55-933a-8116cc856a96",
  "snapshot_schema_version": 1,
  "tau_runtime_version": "...",
  "runtime_config_sha256": "sha256:...",
  "capability_set_sha256": "sha256:...",
  "payload_sha256": "sha256:...",
  "snapshot": {}
}
~~~

Django stores snapshots in a Tau-only TauAgentSessionResumeSnapshot model linked one-to-one to the
AgentSession and linked to its durable TauAgentSessionTurnCommit. It is independent of the ephemeral
runtime lease. Required fields are base_sequence, turn commit, schema/runtime versions,
configuration/capability hashes, payload hash, canonical byte size, typed JSON payload, and
timestamps.

The upload service:

1. authorizes the exact runtime credential target and Tau AgentSession;
2. validates the live holder and lease token;
3. resolves TauAgentSessionTurnCommit by the supplied turn UID and requires its immutable
   next_sequence to equal base_sequence, which also proves the snapshot is not ahead of durable
   history without a second aggregate query;
4. recomputes the canonical payload bytes and SHA-256;
5. enforces a configurable maximum payload size, initially 2 MiB;
6. stores only a snapshot newer than the currently stored base sequence; and
7. treats an exact repeat as idempotent.

An older delayed upload returns HTTP 200 with applied=false and the current snapshot metadata. It
cannot replace a newer snapshot. Snapshot data is stored directly in Django/PostgreSQL and returned
inside the HTTP-compressed bootstrap response. The initial design does not add a second object-store
download to the cold path.

Snapshot production and upload occur after canonical entries and turn state are durable. They do not
delay [DONE]. If Astro exits or upload fails, the previous snapshot plus the canonical entry delta
remains sufficient. If no snapshot exists, the bootstrap full-history fallback remains correct.

Bootstrap selects a snapshot only when:

- its snapshot schema appears in supported_snapshot_schema_versions;
- its Tau runtime version is compatible with the requesting Astro runtime;
- its runtime configuration hash matches the current session configuration;
- its capability-set hash matches the current capability bindings; and
- its base sequence is not ahead of durable history.

When compatible, bootstrap returns the snapshot and only entries whose sequence is at or after its
base sequence. Astro verifies payload_sha256, restores the in-memory Tau session, applies the ordered
delta, and confirms that the resulting next sequence equals Django's durable next sequence before
starting the provider.

When missing, oversized, corrupt, or incompatible, bootstrap returns no snapshot and supplies the
canonical paginated history. Astro rebuilds safely, logs the fallback reason, and schedules a fresh
snapshot after the next durable commit. Snapshot failure can degrade performance but never session
correctness or durability.

### 5. Keep MCP as the sole tool and resource catalog

Bootstrap does not copy the MCP catalog. MCP remains the canonical source for Main Sequence tools and
resources.

Astro introduces a process-scoped Main Sequence MCP runtime that:

1. uses the shared RuntimeCredentialAuth;
2. initializes one connection and downloads tools/resources once per process or catalog revision;
3. supplies the immutable catalog to loaded AgentSessions;
4. preserves request_id, agent_session_uid, turn_uid, and agent_run_uid on every call;
5. reconnects and refreshes after transport failure or revision change; and
6. closes only during process shutdown.

It must not introduce global head-of-line blocking. Mutations remain ordered. Calls advertised as
both read-only and idempotent may use bounded concurrency, consistent with ADR 48. Catalog reuse does
not remove session/environment authorization from individual tool calls.

Astro caches immutable capability bodies by content hash. Session-specific bindings are still
loaded for every cold session.

### 6. Runtime activity is monotonic telemetry, not a provider barrier

Astro will not await a remote activity response before provider execution.

The Tau child runtime-state row adds a lease-scoped non-negative activity_sequence, reset to zero on
new lease acquisition. Astro assigns increasing sequence numbers.

The existing activity endpoint remains backward compatible and accepts exactly one concurrency mode:

- existing expected_activity_revision strict transitions; or
- new activity_sequence monotonic publication.

Under monotonic publication:

- a greater sequence applies the state and increments server activity_revision;
- an equal sequence with equal state is an idempotent replay;
- an equal or lower stale sequence with different state returns HTTP 200, applied=false, and current
  state;
- holder, lease, harness, and activity/turn pairing validation remain mandatory; and
- an old lease cannot publish because its token is invalid.

Responses add activity_sequence and applied without removing existing fields.

Astro maintains one coalescing activity publisher per loaded session. Load completion schedules idle;
turn start schedules working; failure/cancellation schedules recovery. Publications are retried and
logged but do not delay provider execution or user-visible errors.

The first input batch also carries working for the turn. This converges durable input and canonical
activity without a separate blocking PATCH.

Cancellation is accepted for a live Tau runtime lease even if Django has not yet observed working.
An activity race must not prevent cancellation of real provider execution. Astro continues observing
cancellation through lease renewal.

### 7. Commit output and final activity atomically

The existing Tau append-batch request is extended additively:

~~~json
{
  "lease_token": "...",
  "expected_sequence": 41,
  "entries": [],
  "turn": {
    "turn_uid": "b1889848-0034-4f55-933a-8116cc856a96",
    "phase": "committed",
    "activity_sequence": 3
  }
}
~~~

Supported phases are:

- started: append input and publish working for turn_uid;
- progress: append intermediate entries while retaining working; and
- committed: append final entries, record the committed turn, set idle, and clear active_turn_uid.

The committed phase creates or exactly replays a Tau-only TauAgentSessionTurnCommit row containing
AgentSession, turn_uid, next_sequence, and committed_at. AgentSession plus turn_uid is unique. A
replay with a different next_sequence is rejected. This durable row survives lease release and gives
asynchronous snapshot publication an immutable sequence boundary even when a newer turn has already
committed.

The Tau child runtime-state row adds nullable bootstrap_request_uid and last_committed_turn_uid plus
the non-negative activity_sequence. The migration adds no column or behavior to AgentSessionLease,
Pi checkpoints, or Astro Pi.

Committed processing performs one transaction:

1. lock AgentSession, active lease, and Tau runtime-state in the established order;
2. validate harness, holder, lease, expiration, sequence, turn UID, and activity sequence;
3. append/replay the bounded ordered entry batch;
4. create or replay the TauAgentSessionTurnCommit at the resulting next sequence;
5. store last_committed_turn_uid as the current-state projection;
6. set idle, clear active_turn_uid, and apply activity_sequence; and
7. return the durable next sequence, turn commit, and canonical runtime state.

Empty entries remain invalid ordinarily. They are allowed only for an idempotent committed fallback
whose expected_sequence equals the durable next sequence. This handles final output already being in
flight when Tau emits agent_settled.

Astro storage attempts to attach committed to the final pending output batch, making one common-path
request after model output. If output is already durable, it sends the empty idempotent fallback.

~~~text
model output complete
  -> emit finish
  -> await atomic durability/turn-commit acknowledgement
  -> emit [DONE]
~~~

The happy path removes working-to-persisting and persisting-to-idle PATCHes. Persisting remains a
diagnostic/recovery state for compatibility, not a mandatory transition.

If persistence/commit fails after finish, Astro emits the explicit saving error and does not emit a
successful [DONE]. It blocks another turn for that runtime and follows ADR 48 lease-loss/eviction
behavior.

### 8. Emit immediate, truthful lifecycle information

Astro emits an assistant-ui-compatible lifecycle data event immediately after accepting a stream.
Phases are loading_session, generating, saving, and durable.

This improves perceived responsiveness without claiming model output started. The timing report
measures first lifecycle event and first text token separately. A lifecycle event cannot satisfy the
TTFT budget.

### 9. Make latency attributable by AgentSession

Every new Astro and Django phase log includes, when available:

- request_id;
- agent_uid;
- agent_session_uid;
- agent_run_uid;
- turn_uid;
- coding_agent_service_uid;
- organization_environment_uid;
- dependency operation/backend operation;
- duration_ms;
- outcome; and
- a bounded phase discriminator.

The canonical log endpoint remains:

~~~http
GET /api/v1/agent-sessions/{agent_session_uid}/logs/
~~~

It always applies the exact agent_session_uid as a trusted backend filter. A user filter may narrow
but never remove or replace that boundary. Tests prove exclusion of another session using the same
Agent and service.

Required phase events include:

- runtime.auth.exchange.completed;
- runtime.bootstrap.completed;
- runtime.bootstrap.history.completed;
- runtime.bootstrap.capabilities.completed;
- runtime.bootstrap.provider.completed;
- runtime.snapshot.restore.completed;
- runtime.snapshot.upload.completed;
- runtime.mcp.catalog.completed;
- runtime.activity.publish.completed;
- runtime.turn.commit.completed; and
- runtime.persistence.durable.

Credentials, lease tokens, prompts, assistant text, tool arguments/results, capability bodies, and
MCP resource bodies remain excluded.

## tdag-django implementation map

| File or area | Required change |
| --- | --- |
| timeseries_orm/tdag/pod_manager/services/runtime/knative/credentials.py | Remove refresh, routability repair, and reconciliation enqueue from exchange. |
| timeseries_orm/tdag/pod_manager/services/runtime/knative/request_auth.py | Add the dedicated authorization query and reusable runtime context. |
| timeseries_orm/timeseries_orm/settings_base/base.py | Add configurable persistent DB connections and health checks. |
| timeseries_orm/agents/models.py and new migrations | Add Tau-only bootstrap/activity fields, TauAgentSessionTurnCommit, and TauAgentSessionResumeSnapshot. |
| timeseries_orm/agents/serializers.py | Add bootstrap, snapshot upload/metadata, capability advertisement, monotonic activity, turn lifecycle, responses, and errors while preserving strict activity requests. |
| timeseries_orm/agents/services/tau_bootstrap.py | Orchestrate existing lease, compatible snapshot/delta or full history, capability, provider, and state services. |
| timeseries_orm/agents/services/tau_snapshots.py | Validate, hash, bound, store, supersede, and project opaque Tau resume snapshots. |
| timeseries_orm/agents/services/tau_runtime.py | Apply monotonic publication and revised cancellation acceptance. |
| timeseries_orm/agents/services/tau_entries.py | Apply started/progress/committed atomically and create/replay the durable Tau turn-commit boundary. |
| timeseries_orm/agents/harness_backends/tau.py | Dispatch new behavior only for Tau. |
| timeseries_orm/agents/views.py | Add documented Tau bootstrap and resume-snapshot actions plus additive schemas. |
| request timing/observability | Record auth, authorization, DB, service, serialization, and total duration. |
| top-level docs/ | Update runtime credential, AgentSession, Tau persistence, logging, and API docs. |

All Django test commands use --keepdb --noinput.

## Astro Tau implementation map

| File or area | Required change |
| --- | --- |
| src/astro/backend/auth.py | Add startup prefetch/background refresh while retaining single-flight exchange. |
| src/astro/backend/routes.py | Add the canonical Tau bootstrap route. |
| src/astro/backend/models.py | Add typed capability advertisement, bootstrap, resume snapshot, monotonic activity, and turn lifecycle contracts. |
| src/astro/backend/client.py | Consume bootstrap/snapshot/timing metadata and send snapshot, extended batch, and commit requests. |
| src/astro/backend/mcp.py | Move MCP connection/catalog ownership to process scope with safe bounded concurrency. |
| src/astro/runtime/manager.py | Use snapshot-aware bootstrap and remove awaited activity from provider/settlement paths. |
| src/astro/runtime/snapshots.py | Serialize, hash, validate, restore, and asynchronously upload deterministic Tau resume snapshots. |
| src/astro/runtime/session.py | Preserve finish before durability and [DONE] after commit. |
| src/astro/sessions/storage.py | Attach turn phases, combine output/commit, and implement the fallback. |
| src/astro/api/chat.py and src/astro/protocols/assistant_ui.py | Emit lifecycle data without changing finish/done semantics. |
| settings and logging | Add readiness, phase budgets, and canonical timing/correlation. |

Astro Pi is explicitly outside scope. No Pi source, model, migration, checkpoint contract, Compose
file, test, branch, or deployment is modified.

## API compatibility

This decision is additive:

- existing AgentSession, lease, entries, capabilities, provider credential, and activity routes
  remain;
- append-batch requests without turn preserve ADR 48 behavior;
- expected_activity_revision remains valid;
- bootstrap is Tau-only;
- resume-snapshot upload is Tau-only and additive;
- monotonic fields are additive; and
- non-Tau bootstrap/turn lifecycle returns the canonical unsupported-runtime-operation response.

The existing AgentSession detail/runtime-state response advertises the additive contract:

~~~json
{
  "runtime_capabilities": {
    "tau_runtime_bootstrap": "v1",
    "tau_resume_snapshot": "v1",
    "tau_activity_sequence": "v1",
    "tau_turn_commit": "v1"
  }
}
~~~

These keys are present only when the session uses Tau and the backend implements the complete
corresponding operation. Astro Tau switches only after all four values are advertised. During a
rolling deployment it may retain the ADR 48 path and must log the selected contract. It must not use
404 probing as capability discovery, and no request is translated into Pi checkpoint behavior.

## Failure and consistency behavior

- Authentication never starts reconciliation.
- Bootstrap failure before lease acquisition leaves no lease.
- Failure after acquisition is replayable with the same request UID and holder.
- Provider/local construction failure releases the lease best-effort.
- Failed asynchronous activity does not fail a provider turn; it is retried and logged.
- Lease loss still fails the turn and invalidates pending persistence.
- Stale activity cannot overwrite a later committed idle state.
- Entry and turn commit succeed atomically or do not change durable state.
- [DONE] never precedes durable output and committed turn state.
- Snapshot creation/upload never delays [DONE].
- A missing, stale, corrupt, oversized, or incompatible snapshot falls back to canonical entries.
- An older asynchronous snapshot upload cannot replace a newer stored snapshot.
- A snapshot base sequence must match its immutable durable Tau turn-commit boundary.
- The next turn waits for unresolved prior durability.

## Test and acceptance plan

### tdag-django

Focused tests prove:

1. exchange never calls runtime refresh, Kubernetes, or reconciliation enqueue;
2. exchange rejects unknown, mismatched, revoked, rotated, or invalidly bound credentials;
3. request authorization reuses one context and preserves immediate revocation;
4. bootstrap rejects non-Tau and unauthorized sessions;
5. bootstrap returns exact session, lease, compatible snapshot plus delta or full history,
   capabilities, provider, and activity;
6. bootstrap replay returns the same lease and a different holder conflicts;
7. committed batch creates/replays one immutable Tau turn-commit boundary;
8. snapshot upload verifies lease, exact turn-commit base sequence, payload hash, and size;
9. exact snapshot replay is idempotent and an older delayed upload cannot replace a newer snapshot;
10. incompatible/corrupt snapshot metadata is excluded from bootstrap without hiding canonical
   history;
11. monotonic activity applies newer state, replays equal state, and ignores stale state;
12. started/progress/committed are atomic and idempotent;
13. an empty batch is accepted only for an exact committed fallback;
14. commit replay cannot duplicate entries or increment activity twice;
15. cancellation works despite activity publication lag;
16. no migration or behavior touches Pi; and
17. session logs exclude another AgentSession sharing the Agent/service.

### Astro Tau

Focused tests prove:

1. readiness waits for startup auth and MCP catalog initialization;
2. concurrent token requests use one exchange;
3. one bootstrap replaces session/lease/provider/capability/history fan-out;
4. a compatible snapshot restores Tau and applies only its ordered delta;
5. hash/version/configuration mismatch falls back to canonical entries;
6. snapshot generation/upload starts only after durability and never delays [DONE];
7. multiple sessions reuse one MCP catalog with correct context;
8. provider execution does not await activity;
9. activity publication coalesces and never regresses;
10. input persistence carries started while the provider runs;
11. final output and commit use one batch where possible;
12. the empty commit fallback is idempotent;
13. finish precedes durability and [DONE] follows it;
14. persistence failure after finish produces an explicit saving error; and
15. no Astro Pi module or adapter is imported.

### Real conversation

The Docker Compose test runs:

1. a cold turn after recreating Astro Tau;
2. a warm second turn in the loaded session;
3. a different session in the same process, proving MCP reuse; and
4. restart/resume from the compatible backend snapshot plus delta, referencing exact prior durable
   content; and
5. forced snapshot incompatibility followed by correct full-history fallback.

Before the snapshot restart case, the test waits up to a separate bounded snapshot-publication
deadline for runtime.snapshot.upload.completed. That wait is reported independently and begins only
after [DONE]; it is never included in finish-to-[DONE] durability latency.

It proves responses come from the Compose Astro container and local Django backend. Kubernetes is not
contacted in the measured conversation path.

The report separates response headers, lifecycle event, bootstrap, snapshot selection, snapshot
restore, delta application, full-history fallback, platform pre-provider,
provider-request-to-first-token, first text, streaming, finish, durability/commit, asynchronous
snapshot upload, [DONE], total, and restart-to-ready.

| Measurement | Initial local budget |
| --- | ---: |
| Response headers | <= 0.250 s |
| First lifecycle event | <= 0.500 s |
| Credential exchange p95 | <= 0.300 s |
| Warm platform overhead before provider | <= 0.250 s |
| Cold platform overhead with compatible snapshot | <= 0.750 s |
| Cold full-history fallback overhead | <= 2.000 s |
| Local snapshot validation and restore | <= 0.100 s |
| Asynchronous snapshot publication after [DONE] | <= 2.000 s |
| Warm TTFT | provider latency + <= 0.250 s platform overhead |
| Snapshot cold TTFT | provider latency + <= 0.750 s platform overhead |
| Fallback cold TTFT | provider latency + <= 2.000 s platform overhead |
| Append/commit request p95 | <= 0.500 s |
| finish to [DONE] | <= 1.000 s |
| Restart to ready | <= 10.000 s |
| Any critical local Django request | <= 0.750 s |

A lifecycle event does not satisfy TTFT. Provider latency is recorded separately so it cannot hide a
platform regression and a fast provider cannot hide slow Astro/Django work.

The table is the co-located Django/PostgreSQL target profile. The executable main-orchestrator
development test uses a separate 2.000-second finish-to-[DONE] ceiling because Astro and Django run
locally while Django reaches the development database through a host proxy. It continues reporting
the actual duration, and the 1.000-second co-located target remains unchanged. This prevents WAN and
proxy distance from making the local integration test flaky without accepting that distance as the
production architecture.

The deterministic restart case requires a 100 percent snapshot hit rate after a successful snapshot
publication. Production metrics separately report snapshot availability, compatibility, restore
failure, delta length, payload bytes, and fallback-reason rates.

### Implementation verification on 2026-08-27

The opt-in test force-recreated `astro:tau`, used the main Astro orchestrator AgentSession through
the local Compose HTTP port, completed cold and warm turns, restarted the same container, and
recovered the exact prior token from one compatible Django snapshot. Kubernetes was not part of the
measured conversation path; it was used before the test only as the credential source.

| Measurement | Fresh process/session | Warm turn | After restart |
| --- | ---: | ---: | ---: |
| Ready | 3.041 s | n/a | 4.540 s including restart command |
| Response headers / first lifecycle | 0.032 s | 0.007 s | 0.008 s |
| First text | 5.564 s | 4.725 s | 7.086 s |
| finish to durable `[DONE]` | 1.674 s | 1.422 s | 1.458 s |
| Total turn | 7.331 s | 6.300 s | 8.666 s |
| Asynchronous snapshot publication | 1.970 s | 1.655 s | not awaited |

All executable UX gates passed, the restarted process reported `snapshot_restore_count=1`, and no
session, task, checkpoint, or parallel persistence contract was created. Compared with the recorded
baseline, fresh TTFT fell from 17.764 to 5.564 seconds and total time fell from 21.299 to 7.331
seconds. The remaining 1.4 to 1.7-second durability interval is attributable to immediate durable
authorization plus the remote development-database transaction; production/co-located percentile
validation remains a deployment gate.

## Delivery sequence

1. Add Django phase measurements and capture a baseline.
2. Remove exchange reconciliation and enable safe persistent DB connections.
3. Add startup auth/MCP readiness and process-scoped MCP ownership.
4. Add the Django snapshot model/upload contract and Astro deterministic serializer/uploader.
5. Add snapshot-aware Django bootstrap and switch Astro Tau behind capability detection.
6. Add monotonic activity and atomic turn lifecycle.
7. Remove awaited activity and switch final settlement to turn commit.
8. Extend the real conversation test with snapshot cold, fallback cold, warm, second-session, and
   restart cases.
9. Run focused Django tests with --keepdb --noinput, the full Astro Tau suite, static checks, and the
   local real conversation test.
10. Deploy Django before Astro Tau, observe correlated percentiles, and retain the ADR 48 fallback for
   one rollback window.
11. Remove fallback only after all supported backends advertise the new capability.

## Consequences

Benefits:

- warm provider execution has no synchronous activity dependency;
- cold loading uses one canonical backend orchestration operation;
- compatible resume snapshots make cold restoration approach warm-session platform latency;
- authentication cannot be delayed by Kubernetes or deployment repair;
- MCP remains the sole catalog while discovery is reused;
- durable output and final activity cannot disagree;
- cancellation survives activity races;
- session-filtered logs attribute remaining latency; and
- Pi remains isolated.

Costs:

- bootstrap is a larger coordinated contract;
- resume snapshots add a derived Tau-only model, serializer compatibility, bounded storage, and
  asynchronous refresh behavior;
- monotonic activity requires a Tau-only migration and compatibility mode;
- process-scoped MCP requires bounded concurrency and robust reconnect;
- history/capabilities require strict size and pagination limits;
- readiness performs more useful startup work; and
- deployment spans two repositories.

## Alternatives rejected

### Increase timeouts

The requests already finish; larger timeouts preserve poor UX.

### Enqueue reconciliation asynchronously during exchange

Broker publication may still block, and authentication is the wrong deployment-repair trigger.

### Run strict activity PATCHes in arbitrary background tasks

Strict revisions can reorder, conflict, or leave stale final state. Lease-scoped monotonic ordering
and atomic commit are required.

### Emit an immediate SSE event without reducing TTFT

Lifecycle feedback helps but does not fix 16 to 18 seconds before model output.

### Copy the MCP catalog into bootstrap

That creates a second tool/resource contract and violates MCP as the source of truth.

### Persist only raw canonical entries

Raw entries are sufficient for correctness but force every cold process to replay work already
completed by the prior runtime. The resume snapshot is a disposable acceleration artifact; entries
remain canonical and provide the fallback.

### Cache runtime authorization without a revocation contract

An eventually consistent cache could allow revoked credentials. This decision preserves immediate
durable checks.

### Implement in Astro Pi

The measured runtime, persistence, activity, and test are Tau-specific. Pi is out of scope.

## Relationship to earlier decisions

This ADR amends ADR 48. It preserves ordered atomic batches, lease ownership, Tau-only runtime state,
session-filtered logging, and two-phase finish/[DONE] settlement. It replaces the assumption that
every lifecycle transition must be synchronously published as a separate backend request.

It also refines ADR 45's nonblocking I/O direction by specifying the backend aggregation, monotonic
activity, compatible resume snapshots, authentication boundary, MCP reuse, and measurable UX
contract required end to end.
