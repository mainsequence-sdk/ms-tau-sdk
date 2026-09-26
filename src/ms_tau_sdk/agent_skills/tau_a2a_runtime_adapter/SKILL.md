---
name: tau-a2a-runtime-adapter
description: Implement and diagnose the TAU host side of Main Sequence A2A without duplicating the Django-owned protocol, authorization, discovery, and task-lifecycle contract.
---

# TAU A2A Runtime Adapter

Use this skill only for `ms-tau-sdk` host behavior. Read the platform-owned `a2a_communication`
skill first for canonical tool inputs, discovery, authorization, wire envelopes, task lifecycle,
idempotency, and retry semantics.

## Ownership boundary

Django owns:

- canonical `a2a.*` MCP schemas and platform discovery;
- AgentSession and AgentTask lifecycle;
- exact runtime-access authorization;
- caller-session proof validation; and
- direct-runtime protocol and response-kind semantics.

The SDK owns:

- projecting the authenticated Main Sequence MCP catalog into a TAU session;
- preserving each tool's canonical name and metadata through host normalization;
- privately attaching active caller-session proof when a tool advertises
  `mainsequence.ai/requires-caller-session-proof/v1: true`;
- translating inbound A2A requests into the shared TAU runtime; and
- translating runtime events and results into validated A2A responses.

Do not hard-code behavior from a rendered host-tool name. Inspect the canonical tool record and its
`_meta` marker. The proof belongs under request `_meta` key
`mainsequence.ai/caller-session-proof/v1`; it is never a model-visible argument.

## Managed runtime behavior

For a protected call, the active session must provide its exact UID, lease holder, and lease token.
The SDK attaches that proof only to the individual MCP call that requested it. It must not expose,
log, persist, or reuse the proof as general authorization.

## Send Work To Another Agent

For a deployed Agent, use the projected `a2a.send_message` MCP operation. Django creates or
resolves the target `AgentSession`, waits for its runtime to become ready, and dispatches the
message. For asynchronous work, use the returned Task handle with `a2a.wait_task`.

For local Tau development, start `ms-tau` and wait for its local `/ready` result. The public A2A
REST and JSON-RPC surfaces support Message and Task execution against the workspace-local SQLite
store, including streaming, list/get/cancel/subscribe, and continuation. An A2A protocol Task does
not require a platform AgentSession.

Outbound work to a deployed Agent still uses `a2a.send_message` through Main Sequence MCP. Message
and Task-with-polling flows use the authenticated user. `resume_caller` requires a registered
platform callback target and is therefore unavailable to an unregistered local process.

If proof is unavailable or stale, surface the platform error. Never fabricate an AgentSession,
lease, target identity, or Environment selection. The SDK does not reinterpret requester/responder
wire direction as caller identity.

## Local-mode behavior

Local mode has no registered Agent or AgentSession and therefore cannot supply deployed caller
proof or appear in platform discovery. Public local Message and Task coordination is nevertheless
supported through local context identities and SQLite persistence. Only internal backend dispatch,
caller delivery, push notifications, platform discovery, and `resume_caller` retain the documented
capability boundary. Main Sequence MCP remains available for authenticated-user A2A semantics.

## Diagnostics

When an A2A operation fails, identify the boundary before changing code:

1. Catalog projection: canonical tool or `_meta` marker missing.
2. Host adaptation: private proof not attached to the marked call.
3. Platform authorization: proof rejected, target disallowed, or Environment mismatch.
4. Runtime access: endpoint, token, or release bundle stale.
5. Wire validation: request/response role, context, extension, or payload invalid.
6. Task lifecycle: fence, lease, cancellation, event order, or retry semantics violated.

For Task lifecycle diagnosis, derive terminality from the Task state; never introduce a second
terminal flag. Normal execution errors must settle `failed` and streaming must emit that
authoritative terminal status update. `task_terminalization_unknown` means settlement durability
is unknown and belongs to the recovery owner. Local SQLite recovery is SDK-owned; managed dispatch
recovery is backend-owned. Do not retry a stale working attempt when project or MCP side effects may
have occurred unless a checkpoint or idempotency contract proves replay safe.

Treat Task Messages, Artifacts, Tau entries, Task events, and logs as separate ontologies. Use
`historyLength` only for the bounded durable Message tail. Omission means the SDK default of 100,
zero omits history without a tail read, and positive values return the latest bounded tail in
oldest-to-newest order. Public A2A v1 roles are `ROLE_USER` and `ROLE_AGENT`; persistence uses the
Main Sequence requester/responder direction values. Never expose internal Tau entries, reasoning,
tool traffic, prompts, or logs as public Task history.

Allocate the Tau turn UID before attempt start and pass that exact UID to the runtime prompt. A
Task attempt owns one turn and one half-open entry interval with a `committed` or `abandoned`
resolution. Do not correlate by timestamps. A status settlement is absent or one complete durable
responder Message; never send the removed ad hoc status-detail shape. Status events reference that
Message and consumers reload the Task snapshot.

Change this SDK only for catalog projection, proof attachment, runtime execution, or transport
translation defects. Platform contract changes belong to Django.
