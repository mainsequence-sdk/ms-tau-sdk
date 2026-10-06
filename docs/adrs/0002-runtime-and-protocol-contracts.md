# ADR 0002: Runtime and Protocol Contracts

Status: Accepted

Date: 2026-09-16

Amended 2026-09-19 by [ADR 0007](./0007-retire-agent-targeted-sessionless-responses.md):
agent-targeted, sessionless model responses are no longer an SDK contract.

Amended 2026-09-24 by [ADR 0011](./0011-independent-base-tool-and-main-sequence-mcp-exclusion.md):
Main Sequence MCP projection is optional through a process setting.

Amended 2026-10-05: the runtime credential can be proven with the projected workload identity token
read from `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE` instead of the secret, and the exchange retries
throttled or temporarily unavailable answers a bounded number of times
([issue #56](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/56)).

Amended 2026-10-05 by
[ADR 0019](./0019-verified-request-identity-and-session-ownership.md): a hosted runtime admits a
request only with the platform's signed caller or platform assertion, `create_app()` declares
request identity for the platform launcher, and only a session's owner or an Organization admin
can address that session
([issues #59](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/59) and
[#60](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/60)).

Amended 2026-10-06: the projected workload identity token is the only proof of the runtime
credential, and `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE` is required. The bootstrap secret mode
is removed: the SDK no longer reads `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`, and an exchange
request carries only `credential_id` and `workload_identity_token`.

## Context

The project identity changed, but its useful transport and execution behavior remains necessary.
Those contracts must be adopted explicitly without importing the former deployment topology.

## Decision

The SDK adopts the following contracts:

1. Runtime authentication requires `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` and
   `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE`. The client exchanges the credential for short-lived
   access credentials and never places secrets or tokens in project configuration, prompts, logs,
   error messages, diagnostics, or the environment.
   - The only proof of the credential is the projected workload identity token in that file. The
     client reads the file for every exchange, because the token is rotated, and sends exactly
     `credential_id` and `workload_identity_token`. An unset setting, or a missing, unreadable, or
     empty file, is an error, and there is no other proof to fall back to.
   - An exchange answered with 429 or 503 is retried up to three times, waiting as long as
     `Retry-After` asks, up to 60 seconds, or with exponential backoff without it. A 401 fails at
     once, without a retry.
2. Durable execution attaches to an existing backend session, acquires and renews one lease,
   validates provider-control evidence, restores snapshot/history state, and runs a Tau
   `CodingSession` in the project workspace.
3. Session entries append in ordered atomic batches. A turn becomes complete only after output and
   its commit boundary are durable. Conflicts, lost leases, cancellation, and ambiguous replay are
   explicit failure states.
4. Agent-targeted, tool-free model responses are outside the SDK. Tau execution uses the
   workspace-bound coding runtime through chat or A2A Message/Task surfaces.
5. The SDK preserves its tested chat, session, health, readiness, A2A REST/JSON-RPC, SSE,
   replay, task-control, cancellation, and subscription behavior. The exact operation list is an
   executable contract in `tests/contract/test_http_surface.py`.
6. Provider selection and credentials come from authenticated Main Sequence evidence. The SDK
   validates provider, model, thinking level, media support, transport, and custom endpoint safety
   before constructing a provider.
7. Main Sequence MCP uses the same authenticated client lifecycle. MCP tools and advertised
   resources are projected into durable sessions; caller-session proof remains private host data.
8. Structured logs redact credentials and conversation content. Health and logs may report safe
   composition counts and digests.
9. Application startup and shutdown have one owner. Shutdown stops new work, drains or cancels
   bounded work, closes Tau sessions and MCP, releases leases, and closes the shared HTTP client.
10. A hosted runtime authenticates every inbound request with the platform's signed assertion and
    lets only a session's owner or an Organization admin address that session. ADR 0019 holds the
    contract.

External HTTP field names that are part of an existing service contract remain wire details; they
do not define local SDK runtime roles or deployment modes.

## Verification

Contract and unit suites cover authentication, provider evidence, MCP, durable and local paths,
stream encoding, persistence, snapshots, task replay, cancellation, lifecycle, and shutdown. A
change to a wire or durability contract requires a new or amended SDK ADR and updated black-box
tests.
