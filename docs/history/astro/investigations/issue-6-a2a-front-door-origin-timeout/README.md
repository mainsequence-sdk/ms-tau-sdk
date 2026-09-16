# Issue 6: Direct A2A Message Origin Timeout

Status: Engineering analysis and required-resolution proposal

Issue: [astro-tau #6](https://github.com/Main-Sequence-Server-Side/astro-tau/issues/6)

Date investigated: 2026-08-28

Primary owners: Astro Tau, tdag-django, infrastructure, and mainsequence-sdk

Implementation status: Not implemented by this document

## Purpose

This document is the authoritative engineering analysis for issue #6. It records the verified
production evidence, separates the triggering infrastructure condition from the protocol defect,
and defines the required cross-repository resolution.

The incident affected a session-backed direct A2A `message:send` request with
`configuration.responseKind = "message"`. It was not an Agent Task and must not be fixed by silently
converting the request into one.

## Executive conclusion

The direct A2A turn was correct inside Astro and Tau, but the public request could not deliver the
result because Azure Front Door closed the connection at its hard 240-second response-silence
limit. The model was not thinking for four minutes. Approximately 209 seconds were consumed before
Astro received the request while the Knative service scaled from zero, waited for AKS capacity,
pulled a 1.39 GB image, started, and became ready. Astro then completed the request in approximately
42.9 seconds, about six seconds after Front Door had already returned an HTML 504 to the caller.

The incident has two independent causes:

1. **Infrastructure trigger:** a supposedly ready runtime had no warm pod or immediately schedulable
   capacity, so cold activation consumed almost the complete edge budget.
2. **Protocol correctness defect:** direct Message execution has no durable idempotency/recovery
   record. Once the transport result is ambiguous, retrying the same A2A `messageId` can execute a
   second turn.

Increasing Astro, Knative, or NGINX timeouts cannot fix this. Front Door is already configured at
Azure's maximum. The required resolution is to:

- keep runtime activation away from the accepted-message critical path;
- make the inner gateway return a machine-readable timeout before Front Door's deadline;
- reserve and persist direct Message execution by A2A `messageId` without creating an `AgentTask`;
- allow a same-ID retry to recover the exact result without re-execution; and
- distinguish execution, durability, origin response transmission, and end-client delivery in
  logs and state.

## Scope

### In scope

- `POST /api/a2a/v1/message:send` in direct Message mode
- session-backed Tau execution and persistence
- Azure Front Door and the coding-agent NGINX gateway
- Knative scale-from-zero and AKS node provisioning
- tdag-django runtime readiness and direct-message idempotency
- Astro disconnect, deadline, replay, and delivery semantics
- SDK handling of ambiguous transport failures
- live cross-layer observability and end-to-end tests

### Out of scope

- silently changing the response to an A2A Task
- raising Front Door beyond 240 seconds
- treating generic `/api/agents/{agent_uid}/responses` as a replacement for session-backed A2A
- weakening Tau durability or runtime authorization
- claiming exactly-once behavior for an arbitrary external tool that does not itself provide an
  idempotency contract
- implementing the changes in this investigation document

## Terminology and invariants

### Direct Message

A direct Message request asks Astro to return a completed A2A Message. It does not create an
`AgentTask`, Task state, Task events, or a Task polling contract.

### Execution completion

The Tau harness produced a terminal answer for the accepted turn.

### Durability completion

The turn's canonical Tau entries and turn commit are stored by tdag-django.

### Origin response completion

Astro wrote the response through ASGI to its immediate upstream. This does not prove that Front Door
or the final caller received the response.

### End-client delivery

The public caller received and parsed the response. Astro alone cannot prove this. Correlated gateway
and edge telemetry is required.

### Required invariants

1. Direct Message mode never creates an `AgentTask`.
2. A protocol `messageId` is mandatory for a direct Message.
3. The same session, message ID, and normalized request execute at most one Tau turn.
4. Reusing the same ID with different normalized content is rejected.
5. A completed same-ID retry returns the exact persisted A2A response Message.
6. An indeterminate execution is never automatically rerun.
7. The gateway must terminate silent origin waits before the outer Front Door deadline.
8. Runtime deployment readiness must not be presented as proof of a warm serving pod.

## Verified incident identity

| Field | Value |
| --- | --- |
| AgentSession UID | `d5058dbc-3ed9-4269-bab6-52c52d4b790a` |
| A2A message ID | `main-final-48b2608c-q1` |
| Astro request ID | `d44e2bf361e15f36b80a8bcaea3571d1` |
| Tau turn UID | `38a87fb6-fc5e-4b42-85e1-bc3a083a2841` |
| Runtime revision | `c-09327136-ec6c-441e-8dbd-846f292b4e05-00003` |
| Astro bundle | `4.0.2` |
| Tau harness | `0.3.1`, protocol `tau-session-v1` |
| SDK caller | `7.0.2` |
| Public result | Azure Front Door HTML `504 OriginTimeout` |
| Runtime result | Astro HTTP 200; correct answer persisted |

## Exact timeline

Timestamps below are UTC and correlate the coding-agent gateway, Kubernetes events, Astro logs, and
Tau persistence logs.

| Time | Event | Elapsed implication |
| --- | --- | --- |
| 19:44:56 | Public/gateway request begins | Front Door silence budget starts |
| 19:44:59 | Knative scales the revision from zero to one | No warm runtime pod existed |
| 19:45:00 | Runtime pod is created but cannot schedule | Cluster has no immediately suitable capacity |
| 19:45:01 | Pod is nominated to a new node claim | AKS node provisioning begins |
| 19:46:51 | Runtime image pull begins | About 111 seconds lost before image pull |
| 19:48:06 | Image pull completes | Pull took 75.5 seconds; image was about 1.39 GB |
| 19:48:06-07 | Application and queue-proxy containers start | Runtime process startup begins |
| 19:48:17 | Initial startup probe fails | Process is not ready yet |
| 19:48:19 | Knative revision becomes ready | Cold activation took about 200 seconds |
| 19:48:25 | Astro accepts the A2A request | About 209 seconds elapsed before Astro |
| 19:48:33 | Astro session load completes | Session load took 8.229 seconds |
| 19:48:33 | Tau provider turn begins | Model/harness phase begins |
| 19:48:56 | Gateway records downstream close | Front Door has exhausted 240 seconds |
| 19:49:02 | Tau turn and Astro request complete | Tau: 28.768 seconds; Astro HTTP: 42.919 seconds |
| 19:49:04 | Resume snapshot upload completes | Snapshot took 2.524 seconds and was non-critical |

The gateway's terminal record was:

```text
status_code=499
duration_ms=240002
response_size_bytes=0
upstream_status=-
upstream_duration_seconds=236.627
```

`499` is NGINX's record that its downstream client closed the connection. In this topology that
downstream client is Front Door. Zero response bytes and no upstream status show that Front Door
received no origin response headers before closing. Front Door then returned its own HTML 504 to the
public caller.

Astro later logged HTTP 200 because its ASGI send path completed against an origin connection that
had already been abandoned farther upstream. That log is valid as an application-execution result,
but it is not evidence of public delivery.

## End-to-end request path

```text
SDK caller
  -> Azure Front Door                         hard response-silence limit: 240 s
  -> always-running coding-agent NGINX       current upstream read timeout: 3600 s
  -> Knative activator/revision              scale from zero
  -> AKS scheduler/node auto-provisioning    no suitable capacity available
  -> 1.39 GB image pull and container start
  -> Astro Tau                               request finally accepted
  -> tdag-django bootstrap/persistence
  -> Tau/model/tool execution
  -> Astro prepares HTTP 200
  -> response cannot reach the caller because Front Door already closed
```

## Root-cause analysis

### What failed

The public direct Message response contract failed. The caller received infrastructure HTML rather
than either the final A2A Message or a stable A2A error with safe recovery semantics.

### What did not fail

- Authentication and session selection succeeded.
- The request remained in direct Message mode.
- Astro did not create an Agent Task for this turn.
- Tau persisted the input, tool calls, tool results, and final assistant answer.
- The provider produced a valid answer.
- The runtime source and image passed the recorded drift checks.
- The tdag-django batch database operation was not the four-minute bottleneck.

### Triggering infrastructure sequence

The runtime was configured with:

```text
autoscaling.knative.dev/minScale = 0
autoscaling.knative.dev/maxScale = 5
autoscaling.knative.dev/scale-down-delay = 15m
```

The initial scheduling attempt reported insufficient CPU, pod-count pressure, and untolerated node
taints. AKS Automatic nominated a new node, then the node had to pull the complete code repository runtime
image. Scale-from-zero itself is expected; a 200-second activation path for an interactive public
request is not.

The 15-minute Knative scale-down delay did not guarantee node-level warmth. Later node consolidation
also evicted the runtime pod. Knative autoscaling policy and cluster node disruption policy therefore
need to be treated as separate layers.

### Hard transport boundary

The live Front Door profile and Terraform both set `originResponseTimeoutSeconds = 240`. The
infrastructure module documents that value as Azure Front Door Standard/Premium's maximum supported
response-silence timeout. NGINX and Knative cannot extend the already-closed outer connection.

Relevant infrastructure sources:

- `workload_plane/azure/modules/front-door/main.tf`
- `workload_plane/azure/modules/front-door/README.md`
- `workload_plane/azure/modules/k8s-deployments/coding-agent-services/nginx/default.conf.tftpl`
- `workload_plane/azure/modules/aks-cluster-automatic/main.tf`
- `tests/test_runtime_gateway_deadlines.py`

The current infrastructure tests prove that configured timeouts equal the intended provider maxima.
They do not prove that a scale-zero service with an uncached code repository image can produce a direct A2A
response before the edge deadline.

### Readiness semantic gap in tdag-django

`CodingAgentService.is_runtime_routable()` treats a stored ready Knative revision as routable. It
does not establish that a pod is currently running, scheduled, image-ready, and able to answer a
request within the public edge budget.

`CodingAgentRuntimeAccessResolver` currently exposes that projection as `is_ready`. Consequently,
the caller can resolve "ready" token-mode access while the service is at scale zero and the cluster
has no application capacity. The current meanings need to be separated:

- `deployment_ready`: the desired Knative revision exists and is the latest ready revision;
- `activation_state`: whether a serving pod is currently cold, warming, warm, or unknown;
- `observed_ready_at`: when a live application readiness probe last succeeded; and
- `warm_until`: the bounded interval during which the platform is intentionally maintaining
  conversational warmth.

Deployment readiness remains useful, but it must not be described as warm conversational readiness.

### Direct Message idempotency gap

tdag-django has request-hash and `message_id` idempotency for `AgentTaskMessage`. Astro uses that
technology only when `responseKind = "task"`. Direct Message mode calls `_execute_message()` directly
and has no equivalent backend reservation.

Tau entry idempotency is not sufficient. A retry creates a new Tau turn with new entry IDs, so it can
repeat the same logical user request while remaining valid at the entry layer.

The SDK preserves and reports the caller's message ID, and it did not automatically retry this 504.
That was safe behavior. It cannot recover the result until the server implements same-ID replay.

### Disconnect and delivery-observability gap in Astro

The direct handler consumes the request body and then waits for the Tau turn without polling
`request.is_disconnected()`. The logging middleware only learns about `http.disconnect` when the
application reads from ASGI receive. It therefore logged a successful HTTP 200 even though the
gateway had recorded the downstream disconnect six seconds earlier.

`_execute_message()` also calls `manager.mark_response_delivered()` before FastAPI has written the
response. The method actually schedules a resume-snapshot upload; it cannot prove delivery. Snapshot
scheduling and transport delivery must be decoupled and named according to what they really prove.

Relevant Astro sources:

- [`src/astro/api/a2a.py`](../../../src/astro/api/a2a.py)
- [`src/astro/runtime/manager.py`](../../../src/astro/runtime/manager.py)
- [`src/astro/logging.py`](../../../src/astro/logging.py)

## Why obvious fixes are insufficient

### Increase Front Door timeout

Not possible beyond the deployed 240-second provider maximum.

### Increase NGINX or Knative timeout

Both already outlive Front Door. Increasing them lets work continue even longer after the public
caller has been disconnected and makes ambiguity worse.

### Return the persisted answer on the original connection

Impossible after Front Door closes that connection. The result must either be delivered before the
deadline or recovered on a later same-ID request.

### Always set `minScale=1`

This would prevent this exact scale-zero path but can impose permanent per-service cost across many
inactive code repository runtimes. It also does not protect an unusually long model/tool turn from the finite
edge deadline. A bounded warm-lifecycle policy and idempotent recovery are still required.

### Use A2A Task mode

That changes the caller's requested semantics. Task mode is valid only when explicitly selected by
the caller and cannot be the hidden repair for direct Message mode.

### Retry on every 504

Unsafe before direct-message reservation is deployed. A 504 says nothing about whether the turn or
its tools executed.

### Send whitespace as a JSON heartbeat

Streaming insignificant whitespace before a final JSON object may appear to reset an intermediary's
silence timer, but it is fragile, difficult to observe, and not a sound public A2A contract.
`message:stream` is the protocol surface for streaming behavior.

## Required target architecture

The target is a direct Message execution receipt, not an Agent Task.

```text
resolve runtime access
  -> distinguish deployment-ready from warm-ready
  -> asynchronously warm the runtime when necessary
  -> caller waits for a bounded warm-ready result

direct message:send
  -> gateway records origin start and enforces inner deadline < 240 s
  -> Astro validates and normalizes the A2A request
  -> Astro atomically reserves messageId in tdag-django
  -> one owner executes one Tau turn
  -> Tau entries and turn commit become durable
  -> Astro completes the direct-message receipt with the exact A2A Message
  -> if connection is alive, return that Message
  -> if connection is gone, same-ID retry returns that Message without execution
```

### 1. Add a canonical direct-message execution receipt in tdag-django

Introduce a Tau/session-backed model, provisionally named `AgentDirectMessageExecution`. It must not
inherit from, link to, or create `AgentTask` merely to reuse Task behavior.

Required fields:

| Field | Purpose |
| --- | --- |
| `uid` | Internal immutable execution identity |
| `organization_owner` | Authorization and tenant boundary |
| `agent` | Agent identity bound to the session |
| `agent_session` | Canonical conversational session |
| `protocol_message_id` | Incoming A2A `messageId` |
| `request_hash` | Hash of the canonical normalized request |
| `state` | `reserved`, `running`, `completed`, `failed`, or `indeterminate` |
| `holder_id` and fencing token | Single active Astro execution owner |
| `lease_expires_at` | Bounded ownership, not permission to blindly rerun |
| `turn_uid` / turn-commit relation | Proof of the durable Tau turn |
| `response_message` | Exact canonical A2A Message returned on replay |
| `failure_code` and safe detail | Stable terminal failure replay |
| timestamps | Reservation, start, commit, terminal, and last recovery access |

Required database constraint:

```text
UNIQUE (organization_owner, agent_session, protocol_message_id)
```

The service must reuse the normalized request-hash approach already used by `AgentTaskMessage`, but
the request normalization must include all execution-significant direct Message fields:

- session/context ID;
- role and normalized Parts, including file identity/content;
- accepted output modes and strict-output contract;
- response kind;
- inference configuration that affects execution; and
- relevant protocol extensions.

It must exclude transport-only values such as bearer tokens, trace IDs, request IDs, retry count, and
connection metadata.

#### Reservation outcomes

An atomic reserve operation must return one of:

- `created`: this caller owns the new execution;
- `running`: the same request is already executing;
- `completed`: return the stored response Message immediately;
- `failed`: return the same stable failure without rerunning;
- `indeterminate`: execution ownership was lost after possible side effects; do not rerun; or
- `conflict`: the message ID exists with a different request hash.

The internal runtime API can use separate reserve, complete, fail, and inspect actions. Exact URL
names may be finalized with the implementation ADR, but these semantics are mandatory. Runtime
credential authorization must bind every operation to the exact service and AgentSession.

Completion must validate that the supplied Tau turn commit belongs to the same AgentSession and is
durable before storing `response_message`. An exact repeated completion is idempotent. A different
response or turn for the same receipt is rejected.

#### Crash semantics

A lease expiry alone is not proof that no external tool ran. Automatic takeover is safe only when
the backend can prove execution never crossed the provider/tool dispatch boundary. Otherwise the
receipt becomes `indeterminate` and the same ID returns a stable non-rerunnable error.

This provides at-most-one logical Tau turn and safe replay under the observed disconnect. It does not
pretend to provide exactly-once effects for third-party tools that lack their own idempotency keys.

### 2. Change Astro direct Message execution

The direct Message path must become receipt-owned execution:

1. Validate the A2A request and require `messageId`.
2. Build the canonical execution request used for backend hashing.
3. Reserve the direct-message execution before calling `manager.prompt()`.
4. Execute only when the reserve result is `created` and this Astro holds the fencing token.
5. Persist the Tau input/output and terminal turn commit using the existing ADR 48/49 path.
6. Complete the receipt with the exact final A2A response Message.
7. Return the stored Message, not a separately reconstructed equivalent.
8. On same-ID `completed`, return the stored Message immediately.
9. On same-ID `running`, either coalesce locally with the owner or return a stable retryable
   `direct_message_execution_in_progress` error. Never start another turn.
10. On hash conflict, return a stable non-retryable `message_id_conflict` error.

Astro must monitor disconnect independently after request-body consumption. Once execution is
reserved and provider/tool dispatch has begun, a transport disconnect marks delivery as abandoned
but does not cause an untracked rerun. The owner should continue to a durable terminal receipt unless
the Tau execution can be safely canceled before side effects.

### 3. Add an inner gateway deadline below Front Door

The coding-agent NGINX gateway currently waits 3600 seconds, while Front Door gives it only 240
seconds. This ordering guarantees that Front Door, not the platform gateway, generates the first
timeout.

For Azure, configure an origin silence deadline with a safety margin, initially:

```text
Front Door hard silence limit        240 s
platform gateway upstream silence   210 s
edge delivery safety margin          30 s
```

The values must be variables with tests asserting:

```text
gateway upstream silence timeout < Front Door origin timeout
```

At the inner deadline, NGINX must return a stable JSON/A2A-compatible response such as:

```json
{
  "error": {
    "code": "direct_message_origin_deadline_exceeded",
    "message": "The direct message is still starting or executing.",
    "retryable": true,
    "delivery_ambiguous": true,
    "retry_with_same_message_id": true,
    "request_id": "..."
  }
}
```

The caller already owns the A2A message ID, so the gateway does not need to parse the request body.
It must return the platform request ID for correlation. The precise HTTP status and media type must
be standardized across Astro, the gateway, and the SDK.

This gateway change must be deployed only after direct Message reservation/replay is available.
Changing the current NGINX timeout first would replace an HTML 504 with JSON but would still tell the
caller to perform an unsafe retry.

The gateway should also replace any client-supplied origin timing header and pass a trusted request
start timestamp to Astro. Astro can then avoid starting a turn when cold activation leaves
insufficient time to send even the machine-readable response.

### 4. Separate deployment readiness from warm readiness

tdag-django must preserve fast authentication and avoid putting Kubernetes reconciliation back into
the credential-exchange path. Warm activation belongs to runtime lifecycle orchestration, not auth.

The runtime-access contract should expose:

```json
{
  "deployment_ready": true,
  "activation": {
    "state": "cold | warming | warm | unknown | failed",
    "observed_ready_at": "...",
    "warm_until": "...",
    "retry_after_seconds": 2
  }
}
```

Compatibility can retain `is_ready` temporarily, but it must be documented as deployment readiness
and later deprecated if its name remains misleading.

A dedicated activation operation should:

1. resolve the exact service without mutating credential authentication;
2. trigger a harmless internal readiness request that can scale Knative from zero;
3. poll live application readiness asynchronously;
4. persist a bounded activation state and last successful observation;
5. keep the service warm for a configurable conversational grace period; and
6. return failure before any user Message is accepted when warm activation cannot meet its SLO.

The SDK should resolve and await this bounded activation state before sending an interactive direct
Message. A raw caller may still call the runtime directly, so gateway deadlines and message receipts
remain mandatory.

Warmth can initially be maintained with safe periodic internal readiness traffic during the bounded
grace period. A blanket permanent `minScale=1` for every dormant code repository runtime is not the default
design. If Knative traffic-based warmth proves unreliable, introduce an explicit active-service
minimum-scale policy with cost limits and expiry.

### 5. Fix cluster capacity and image-startup behavior

The infrastructure must define an interactive-runtime capacity SLO rather than relying only on
eventual AKS Automatic node provisioning.

Required work:

- maintain enough schedulable application headroom for at least the expected burst of interactive
  runtime activations;
- separate system-pod capacity from coding-agent workload capacity;
- ensure node taints and runtime tolerations cannot leave the service waiting for an avoidable new
  node;
- define a disruption/consolidation policy that respects the conversational warm grace period;
- measure node-claim-to-ready and pod-scheduled latency;
- reduce the 1.39 GB code repository runtime image and establish a compressed-image size budget;
- maximize reuse of stable base layers so project-source changes do not invalidate provider/runtime
  layers;
- evaluate node image pre-pull or registry/layer caching for approved runtime images; and
- alert when cold activation consumes more than half of the Azure edge budget.

The concrete capacity mechanism may be a bounded warm application node pool, AKS Automatic headroom,
or another supported reservation mechanism. The required outcome is measurable ready capacity, not
a particular provider feature.

Initial engineering SLO candidates, to be validated under cost and load tests:

| Measurement | Proposed target |
| --- | ---: |
| Warm gateway-to-Astro dispatch p95 | <= 2 s |
| Cold activation gateway-to-Astro dispatch p95 | <= 60 s |
| Cold activation gateway-to-Astro dispatch p99 | <= 120 s |
| Runtime image pull p95 on a new node | <= 30 s |
| Azure direct-message platform deadline | <= 210 s |
| Edge safety margin | >= 30 s |

These are platform timing targets, not promises about arbitrary model or external-tool duration.

### 6. Correct Astro delivery and snapshot semantics

`mark_response_delivered()` must not be used to mean both snapshot scheduling and public delivery.

Required states and logs:

- `direct_message.execution.reserved`
- `direct_message.execution.started`
- `direct_message.execution.completed`
- `direct_message.durability.committed`
- `direct_message.origin_response.started`
- `direct_message.origin_response.completed`
- `direct_message.client_disconnected`
- `direct_message.replay.returned`
- `direct_message.execution.indeterminate`

Resume-snapshot upload should be scheduled from durable turn settlement as independent non-blocking
work. It must happen whether the client remains connected or not. Its event names must describe
snapshot scheduling, not response delivery.

An ASGI send-completion hook can prove only that Astro handed the response to its immediate upstream.
The final delivery assessment requires gateway and Front Door logs.

### 7. Change mainsequence-sdk recovery behavior

Until the server receipt is deployed, the SDK must continue not to automatically retry ambiguous
direct Message 5xx responses.

After deployment, the SDK must:

- retain the exact generated or caller-supplied `messageId` for the complete logical operation;
- classify an origin timeout/disconnect as `ambiguous_delivery`, not ordinary request failure;
- expose the message ID, platform request ID, and safe-retry flag in the exception;
- retry only with the exact same message ID and request body;
- never generate a new ID for transport recovery;
- treat `message_id_conflict` as non-retryable;
- poll/retry `direct_message_execution_in_progress` with bounded backoff; and
- return the recovered Message through the same result type as the original successful call.

Automatic same-ID retry should be enabled only when the server advertises or proves receipt/replay
support. This avoids new SDK versions assuming idempotency against old Astro deployments.

### 8. Add Front Door diagnostics and cross-layer correlation

The live Front Door profile had no diagnostic setting. Terraform must enable the supported Front Door
access and health-probe categories and route them to the workload Log Analytics workspace.

Every layer must retain these non-secret correlation fields:

- platform request ID;
- trace ID and span/parent IDs;
- coding-agent service UID;
- AgentSession UID;
- A2A message ID;
- direct-message execution UID;
- Tau turn UID;
- runtime revision and instance UID;
- gateway request duration;
- upstream connect/header/response timings;
- response bytes and upstream status; and
- delivery state and disconnect source.

The gateway already logs `upstream_status` and `upstream_duration_seconds` inside its payload. These
should be promoted to queryable typed fields in the Azure log pipeline.

Add an alert for this fingerprint:

```text
gateway terminates near the configured platform deadline
AND response bytes are zero or only the timeout envelope
AND Astro later commits the same direct-message execution
```

## Error contract

The public error must be generated before Front Door substitutes an HTML page. It must never promise
that execution did not happen unless the receipt proves the turn never started.

| Code | Meaning | Same-ID retry | New-ID retry |
| --- | --- | --- | --- |
| `runtime_warming` | User turn was not dispatched; runtime is warming | Yes | Not needed |
| `direct_message_execution_in_progress` | Receipt exists and one owner is running | Yes | No |
| `direct_message_origin_deadline_exceeded` | Gateway stopped waiting; execution may continue | Yes | No |
| `message_id_conflict` | Same ID was used for different content | No | Only as an intentional new turn |
| `direct_message_execution_failed` | Stable terminal execution failure | Returns same failure | Only as an intentional new turn |
| `direct_message_execution_indeterminate` | Side effects may have occurred; safe takeover is impossible | No automatic retry | No automatic retry |

The response must use `Cache-Control: no-store` and include the platform request ID. It must not
include provider credentials, runtime tokens, lease tokens, prompt content, or internal exception
details.

## Implementation ordering

The deployment order is a correctness requirement:

1. **tdag-django:** add the direct-message receipt model, atomic services, runtime-only APIs,
   migrations, and authorization tests.
2. **Astro Tau:** reserve before execution, complete after durable turn commit, replay completed
   results, detect disconnects, and emit the new logs.
3. **SDK:** understand advertised receipt support and implement safe same-ID recovery.
4. **Infrastructure gateway:** lower Azure's inner upstream-silence deadline below 240 seconds and
   return the canonical retry envelope.
5. **Runtime lifecycle:** deploy warm-activation state, bounded prewarming, capacity headroom, image
   optimizations, and disruption protection.
6. **Observability:** enable Front Door diagnostics and cross-layer alerts before declaring the issue
   resolved.
7. **Production validation:** run cold, delayed, disconnected, replay, and conflict cases through the
   real public Azure route.

The gateway deadline must not be shortened before steps 1 and 2. Otherwise clients receive an
earlier error but still cannot retry safely.

## Required tests

### tdag-django tests

- Atomic duplicate reserve with the same ID/body creates one execution.
- Same ID with a different body returns `message_id_conflict`.
- Only the fencing-token owner can start or complete an execution.
- Completion requires a durable Tau turn commit from the same AgentSession.
- Exact repeated completion is idempotent.
- Completed replay returns byte-equivalent canonical response Message data.
- Expired ownership before provider dispatch can be reclaimed safely.
- Lost ownership after possible tool dispatch becomes `indeterminate` and is not rerun.
- Runtime credentials cannot access another service or AgentSession receipt.
- Receipt creation never creates AgentTask, AgentTaskMessage, or AgentTaskEvent rows.

### Astro unit and integration tests

- Direct Message reserves before calling the Tau manager.
- A completed receipt bypasses model and tool execution.
- A running receipt never starts a second Tau turn.
- Same-ID conflict returns the canonical error.
- Client disconnect after dispatch is observed and logged while one durable execution completes.
- Snapshot scheduling is independent of response delivery.
- Origin response success is not logged as end-client delivery.
- Direct Message execution does not invoke `_create_backend_task()`.

### Infrastructure tests

- Azure Front Door remains at 240 seconds.
- Azure NGINX upstream-silence timeout is below Front Door by the configured safety margin.
- GCP and Azure values are derived from explicit provider budgets.
- The NGINX timeout envelope has the canonical content type, stable error code, request ID, and
  `no-store` header.
- Front Door diagnostic settings target the correct Log Analytics workspace.
- Gateway logs expose typed upstream duration/status and platform-deadline fields.

### SDK tests

- A generated message ID is retained after an ambiguous error.
- Recovery reuses the exact request body and message ID.
- A new ID is never generated implicitly for the same logical call.
- Conflict and indeterminate errors are not automatically retried.
- Receipt support is capability/version gated.

### Real public-route conversation tests

1. **Warm success:** direct Message completes normally and produces one turn.
2. **Cold success:** scale from zero with available capacity; measure every phase and remain within
   the platform deadline.
3. **Forced cold delay:** delay node/image readiness; receive a machine-readable inner-gateway error,
   never Front Door HTML, then recover with the same ID.
4. **Long execution:** exceed the inner gateway deadline; let execution complete durably; recover the
   exact Message with the same ID and prove the provider/tools ran once.
5. **Socket disconnect:** close the caller after dispatch; replay with the same ID and recover one
   result.
6. **Content conflict:** resend the ID with changed text/configuration and receive a stable conflict.
7. **Owner crash before dispatch:** prove safe reclaim without a duplicate turn.
8. **Owner crash after possible tool dispatch:** prove indeterminate handling and no automatic rerun.

Each report must include:

- access-resolution duration;
- cold activation and node-provision duration;
- image-pull duration and image size;
- gateway-to-Astro dispatch duration;
- Astro session-load duration;
- first model event and first text timing;
- Tau execution and durable-commit duration;
- origin response timing;
- public client result; and
- counts of receipts, Tau turns, user entries, assistant entries, and tool invocations.

## Revised acceptance criteria for issue #6

The issue's current test wording says the model/tool turn should last longer than the edge timeout.
That is useful as one recovery test, but it does not reproduce the observed trigger: the actual Tau
turn took 28.8 seconds and cold activation consumed approximately 209 seconds.

Issue #6 should be considered resolved only when all of the following are true:

- A real Azure public-route test covers scale-zero activation plus a direct Message turn.
- The runtime-access contract distinguishes deployment-ready from warm-ready.
- Interactive activation meets the agreed cold-start SLO under expected capacity.
- The inner Azure gateway returns a canonical machine-readable response before Front Door's
  240-second limit.
- Direct Message receipt reservation occurs before Tau execution.
- A same-ID/same-body retry returns the exact completed response without another turn.
- A same-ID/different-body retry is rejected.
- A disconnect after acceptance cannot cause an automatic duplicate execution.
- Astro does not call an origin response "delivered" before the transport writes it.
- Front Door, gateway, Astro, backend, Tau turn, and client logs can be correlated.
- Message mode remains Message mode and creates no Agent Task rows.
- No supported test returns Azure's HTML `OriginTimeout` page.

## Ownership matrix

| Repository | Required ownership |
| --- | --- |
| `astro-tau` | Direct receipt integration, one-owner execution, replay, disconnect handling, origin timing, semantic logs |
| `tdag-django` | Receipt model/services/APIs, request hashing, fencing, Tau commit linkage, activation/readiness contract |
| `infrastructure` | Inner Azure deadline, A2A timeout envelope, warm capacity, disruption policy, image-start metrics, Front Door diagnostics |
| `mainsequence-sdk` | Stable message ID, ambiguous-delivery classification, capability-gated same-ID recovery |

## Operational mitigation before the complete fix

Until receipt/replay is deployed:

- do not automatically retry a direct Message after 504, disconnect, or malformed edge response;
- preserve and report the exact message ID and platform request ID;
- inspect durable Tau history before any manual resend;
- prewarm the exact runtime with a harmless readiness request before a high-value direct turn;
- keep the affected interactive runtime warm during active validation; and
- treat a Front Door 504 as delivery-ambiguous, not proof of execution failure.

These mitigations reduce risk but do not satisfy the issue's acceptance criteria.

## Decisions that require an implementation ADR

This investigation makes the required semantics explicit, but the cross-repository implementation
should receive its own ADR before coding. That ADR must finalize:

- the exact direct-message receipt model and runtime API URLs;
- canonical request normalization and hash versioning;
- ownership lease and indeterminate-state rules;
- public A2A error status/media type;
- Azure and GCP inner gateway budgets;
- warm-lifecycle duration and capacity/cost policy;
- SDK feature advertisement and compatibility behavior; and
- retention/privacy policy for execution receipts.

The ADR must preserve the invariants in this document. In particular, it cannot use Agent Task
persistence as the direct-message receipt or advertise a retry as safe before the reservation is
durable.
