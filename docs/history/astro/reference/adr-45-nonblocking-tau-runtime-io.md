# ADR 45: Nonblocking Tau Runtime I/O

Status: Accepted
Date: 2026-07-27
Implementation Status: Implemented

## Context

Tau persists every completed message as a `MessageEntry` followed by a `LeafEntry`, then calls
`SessionStorage.read_all()` to rebuild its session state. That behavior is inexpensive for Tau's
local JSONL storage but was expensive when Astro mapped each operation directly to Django:

- each entry append blocked the provider critical path on one backend HTTP request;
- each post-append `read_all()` downloaded the complete session again;
- the OpenAI Codex credential resolver hydrated the same provider credential before runtime load,
  model-limit discovery, and the provider request;
- runtime hydration, capabilities, MCP discovery, and history loading ran serially.

The Django runtime lease guarantees that one Astro runtime is the only writer for an active
session. Astro therefore already knows the entries it has accepted locally and does not need to
confirm each append by rereading Django before calling the model.

## Decision

### Session persistence

`BackendSessionStorage` maintains an in-memory ordered entry cache for the lifetime of the
lease-owned runtime.

`append()` updates that cache and enqueues the remote write on one ordered in-process writer. It
does not wait for Django. Message and leaf persistence therefore proceeds concurrently with model
streaming.

`flush()` is the durability boundary. Astro waits for it before emitting Tau's terminal
`agent_settled` event and before explicitly releasing the runtime lease.

No sidecar, task broker, new service, or durable local volume is introduced. Django remains the
durable session store.

### Reads and recovery

The initial runtime load reads all durable entries from Django. Subsequent Tau `read_all()` calls
return a copy of the in-memory cache, including queued entries, without an HTTP request.

Astro rereads Django only for:

- initial runtime loading;
- runtime reload after lease recovery;
- sequence or idempotency conflict diagnosis;
- an explicit storage resynchronization.

A persistence failure prevents normal turn settlement. If eviction cannot flush queued entries,
Astro does not explicitly release the lease; the backend lease expires normally so another runtime
cannot immediately continue from incomplete state.

### Provider credentials

Provider credentials are hydrated once during runtime startup and retained by the loaded provider.
Resolver calls return the cached credential without contacting Django. A credential is hydrated
again only when its declared expiration is within 60 seconds. Concurrent refresh attempts share
one refresh lock.

Runtime startup performs provider hydration and Tau history loading through the bootstrap contract,
then reuses the process-scoped MCP catalog. ADR 53 removed session capability materialization.

## Consequences

- The provider request is no longer blocked by normal message persistence or history rereads.
- The newest queued entries can be lost if the Astro process crashes before the background writer
  reaches Django. This is an accepted tradeoff.
- Normal shutdown and eviction still drain queued writes before releasing ownership.
- A backend write failure may be reported after model output has started, but before the turn is
  declared settled.
- The in-memory cache is valid only while Astro owns the runtime lease. It is not shared between
  replicas and is discarded when the runtime is evicted.
- Slow Django endpoints still need independent profiling and optimization; moving them off the
  provider critical path does not make them efficient.

## Non-Goals

- Exactly-once recovery of entries that existed only in process memory at crash time.
- A distributed persistence queue.
- A local durable session database or filesystem checkpoint.
- Removing backend sequence, idempotency, or lease validation.
