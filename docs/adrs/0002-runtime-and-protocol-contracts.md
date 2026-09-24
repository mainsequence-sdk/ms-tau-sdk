# ADR 0002: Runtime and Protocol Contracts

Status: Accepted

Date: 2026-09-16

Amended 2026-09-19 by [ADR 0007](./0007-retire-agent-targeted-sessionless-responses.md):
agent-targeted, sessionless model responses are no longer an SDK contract.

Amended 2026-09-24 by [ADR 0011](./0011-independent-base-tool-and-main-sequence-mcp-exclusion.md):
Main Sequence MCP projection is optional through a process setting.

## Context

The project identity changed, but its useful transport and execution behavior remains necessary.
Those contracts must be adopted explicitly without importing the former deployment topology.

## Decision

The SDK adopts the following contracts:

1. Runtime authentication requires `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` and
   `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`. The client exchanges them for short-lived access
   credentials and never places secrets in project configuration, prompts, logs, or diagnostics.
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

External HTTP field names that are part of an existing service contract remain wire details; they
do not define local SDK runtime roles or deployment modes.

## Verification

Contract and unit suites cover authentication, provider evidence, MCP, durable and local paths,
stream encoding, persistence, snapshots, task replay, cancellation, lifecycle, and shutdown. A
change to a wire or durability contract requires a new or amended SDK ADR and updated black-box
tests.
