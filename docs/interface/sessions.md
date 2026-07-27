# Session Storage

The backend `AgentSession.uid` is Astro's only durable session identity.
Astro requires `harness=tau` with `harness_protocol=tau-session-v1` before it
loads a session. Missing or contradictory identity is a contract error; Astro
does not default an untyped session to Tau.

Each loaded session holds:

- one backend runtime lease
- one Tau `CodingSession`
- one provider instance
- one backend-backed Tau `SessionStorage`
- one async lock that serializes turns

Session entries are append-only backend rows with an expected sequence and
idempotency key. Astro follows the backend's paginated `entries` response until
all rows are loaded, then loads them directly into Tau. Lease
acquire/renew/release and cancellation are the runtime lifecycle signals;
Astro does not persist load, turn, or worker-status telemetry in Django.
`PATCH runtime_state` is used only by session configuration to update the
existing provider, model, and thinking fields. Idle runtimes are evicted and
leases are released; the next request rebuilds from backend entries.

No session state is stored in JSONL files or shared container volumes.
