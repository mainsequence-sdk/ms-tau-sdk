# ADR 0021: MCP Connections on the Person's Identity

Amended 2026-10-07 for [issue #78](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/78): one
delegation envelope replaces the per-tool labels. Every call made for the work carries
`delegation: <proof for this receiver> | None`, and each receiving tool decides what it needs. The
platform's answer to a turn start is the only source of the person the turn serves.
`platform_client()` replaces `requester_client()`. A turn that serves nobody keeps its MCP tools,
and there is no transition for older SDK releases. Sections 1 to 7, the platform requirements,
the rollout status, the consequences and the acceptance criteria are edited.

Amended 2026-10-07 for [issue #74](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/74):
clarify the documentation's availability boundary. Steps 1 to 3 are SDK source support; the
platform requirements below must also be met before the complete requester/delegation behavior
is available. This clarification makes no new architecture or release decision.

Status: Accepted — implemented in the SDK (steps 1 to 4); it takes effect with the platform's
cutover

Date: 2026-10-07

Amends:

- [ADR 0004: Minimal bundled tool boundary](./0004-minimal-bundled-tool-boundary.md) by replacing
  the single Main Sequence MCP connection with configured MCP connections;
- [ADR 0011: Independent base-tool and Main Sequence MCP exclusion](./0011-independent-base-tool-and-main-sequence-mcp-exclusion.md)
  by applying the exclusion to the platform's own connection only; and
- [ADR 0019: Verified request identity and session ownership](./0019-verified-request-identity-and-session-ownership.md)
  by letting a delegated Agent serve the person who started the work.

## Context

The SDK connects to one MCP server: the Main Sequence platform's own. The client is fixed to the
platform's `/mcp` URL and authenticates with the runtime credential, so every tool call acts as the
Agent's workload, with that workload's grants. The adapter that turns MCP tools into Tau tools is
specific to that server. Nothing can add another MCP server.

The platform also lets a FastAPI application serve its own MCP endpoint at `/mcp`. An application
receives the verified user on every request, as it does for its other routes. An Agent can reach
such an application today only from project code, over plain HTTP, through `requester_client()`.

Before this decision, acting for the person was read-only and available only to project tools.
Without platform support for recording a delegated requester, a delegated Agent serves nobody:
when Agent A, working for a person, delegates to Agent B, B's turns have no requester.

## Decision

### 1. One general MCP connection primitive

The SDK connects to remote MCP servers over Streamable HTTP. Each connection has a name, used as its
tool prefix; a URL; a credential source; and a tool filter. One adapter serves every connection:
prefixing, schema and result projection, safety filters, parallel execution of read-only tools,
and resources. A connection's private metadata is computed for each call, so that it reflects the
turn that makes the call.

The platform's own MCP becomes the first configured connection. It keeps its current extras: the
Environment argument is hidden, the A2A send rules apply, resources are listed in the prompt, and
the operations that return credentials are never offered to the model.

Not supported: MCP servers that Main Sequence does not host, stdio servers, and the SSE transport.
A stdio server would need its program in the deployed image, so it is outside this decision.

### 2. One delegation envelope on every call made for the work

Every call made for the work carries `delegation: <proof for this receiver> | None`: platform MCP
and application MCP tool calls, and project code's calls through `platform_client()` (section 7).

- **The proof.** It is never a bare UID; the receiver finds the person from it.
  - To the platform: the turn's session proof (session, lease holder and lease token). An MCP call
    carries it in its private metadata under `mainsequence.ai/delegation/v1`; a REST call carries
    the platform's delegation headers. The platform finds the person in its own records.
  - To an application: the short-lived token the platform issues for that one application,
    carrying the person. The application cannot reuse it against the platform or forward it.
  - The SDK never sends the platform's session proof to an application.
- **The session.** A hosted Agent also names its calling session on every platform MCP call, in
  `mainsequence.ai/caller-session-proof/v1`. Naming the session never implies a delegation.
- **When.** A delegation is sent exactly when the turn serves a person (section 3). The SDK keeps
  no per-tool labels or lists to decide identity: it only knows how to obtain a proof for each kind
  of receiver, the platform or an application.
- **Housekeeping.** The runtime's own calls (lease, turn activity, entries, snapshots, Task status)
  never carry a delegation.
- **The receiving side.** The platform checks a supplied delegation once, not per tool: the lease,
  24 hours from the original request, stop requests, the Agent being enabled, the person being
  active, and the Environment. An invalid delegation is rejected, never downgraded. Each tool then
  either requires a person (without a delegation it returns its own error) or treats the person as
  optional (without one it uses the caller's own permissions). No tool refuses a valid delegation.
- **Admin approval.** Only an Organization admin can enable an Agent to act for people. For an
  Agent that is not enabled the platform names nobody, so its calls are its own.
- **The risk.** Model-driven code can be steered by prompt injection. With writes, the damage
  reaches as far as the person's ordinary permissions, including administrative ones, for at most
  24 hours after their request and only while the turn or Task serving it runs. Completed changes
  can outlast that access. This is why the admin approval stays, and why the documentation and the
  statement people are shown say so.

### 3. The person a turn serves

When a hosted turn or Task attempt starts, the platform's answer names the person the Agent may act
for, or nobody. That answer is the only source, whoever called: a chat or A2A Message turn, a Task
attempt the platform dispatches or the request runs itself, and a turn the platform starts for a
caller delivery alike. The SDK never reads a person from an assertion claim, a dispatch or a Task
answer. The verified caller of the request supplies the person's teams only when it is that person.

When an Agent working for a person delegates to another Agent, the delegated Agent acts for that
same person:

- the platform records the person on the delegated Task or turn from its own record of the
  delegating work, never from anything an Agent states;
- the limits stay: at most 24 hours from the person's original request, delegation never extends
  it, it ends when the person loses access, and a stop request on the delegating conversation
  withdraws it;
- a chain of delegations keeps the person and the original request time;
- the delegated Agent must itself be enabled to act for its requester;
- a delegation from work that serves nobody passes nobody on.

### 4. Application MCP endpoints

An Agent declares, in its workflow file, the applications whose MCP endpoints it uses. Declaring an
application registers its MCP; it grants no access. Neither the workflow file nor the runtime
carries an application's UID or URL: UIDs differ per Environment and do not exist before the first
deploy. The platform resolves each declared application in the Agent's Environment and hands the
runtime its name and release in the startup data, as `mcp_applications` entries with `name` and
`resource_release_uid`.

Each application gets two tools:

- `<name>__list_tools` asks the application for its tools, with each tool's name, description and
  input schema; and
- `<name>__call_tool` calls one of them by name with its arguments.

Both run inside the turn. For each call the SDK asks the platform for the application's address and
a short-lived token, opens an MCP session to the application's `/mcp` endpoint, and closes it
afterwards. The token carries the delegation of the person the turn serves, when it serves one, and
is the Agent's own otherwise; without a person, the Agent's workload needs its own grants on the
application. The application decides what each call may do. A refused delegated call is reported,
never retried as the Agent. No session, token or catalog is shared between turns or people. The SDK
reads no catalog when the session loads, because no turn is running then; the model lists an
application's tools when it needs them. Local mode has no declared applications.

### 5. Credentials

The SDK holds no long-lived credential for a connection. It uses short-lived tokens that the
platform issues for the turn: for its person, or the Agent's own. Tokens, proofs and assertions
never enter model context, tool arguments or results, session history, the UI stream or logs.

### 6. Local mode

Local mode already acts as the signed-in person. It uses the same connections with that person's
own credentials, and no call carries a delegation.

### 7. Project code

`platform_client()` replaces `requester_client()`, with no alias. It calls a platform path
(`request()`) or another application (`call_release()`), and its `delegation` argument decides,
when each call is made, whether the call carries the delegation:

- `"auto"`, the default: when the turn serves a person, and not otherwise;
- `"none"`: never, so the call is the Agent's own;
- `"required"`: always; when the turn serves nobody the call is refused before it is sent.

An override can only remove a delegation or require one; it can never add a person, because the
platform finds the person in its own records. Outside a turn no call carries a delegation. A
refused delegated call raises an error that is a `PermissionError`; it is never retried without the
delegation.

## Platform requirements

The SDK depends on the platform to:

1. accept the envelope on MCP and REST calls: the session entry names the calling session and never
   selects a person; the delegation entry (`mainsequence.ai/delegation/v1`, carrying the session
   proof's fields) or the delegation headers select the person; check a supplied delegation on
   every call and reject an invalid one without falling back to the workload;
2. name, in its answer to every turn start (a chat or A2A Message turn, a Task attempt and a
   caller-delivery turn), the person the runtime may act for: only a person, only when the Agent is
   enabled to act for people, and nobody otherwise;
3. record the person who started delegated work from its own records, with the limits in section
   3, and deliver caller assertions to Agent runtimes with exactly the base claim set;
4. resolve the applications an Agent declares in its Environment, hand their names and releases to
   its runtime in the startup data (`mcp_applications`), and admit the Agent's own calls at an
   application's `/mcp` when its workload has access; and
5. update the statement people are shown, so every client shows the same words.

## Rollout status

The SDK changes are listed under **Unreleased** in the [changelog](../../CHANGELOG.md#unreleased).
They ship in one cutover with the platform's: neither side keeps the other's previous behavior
alive, and no released SDK version is served alongside. Released 2.0.6 does not send the envelope,
so once the platform changes it no longer acts for people; it is upgraded in the same cutover.

## Consequences

- An Agent never has more access than the person it serves, and broad grants on Agents stop being
  needed for work done for people.
- Every receiver sees the person on every call made for them, even tools that do not use it. An
  application's proof only works inside that application.
- If the delegation expires mid-work (24 hours or a stop), every delegated call fails, including
  calls that could have run as the Agent. Nothing switches identity silently.
- An Agent that an Organization admin has not enabled, and every turn that serves nobody, keeps its
  MCP tools; its calls are its own.
- Agent-to-agent delegation works for the person who started it.
- The security model page and the statement people are shown change: an enabled Agent can change
  things as the person, with the person's ordinary permissions.

## Not in scope

MCP servers that Main Sequence does not host, stdio servers, the SSE transport, per-person consent
for each Agent, and limiting `platform_client()` to the applications an Agent declares.

## Implementation and acceptance

1. Build the connection primitive and make the platform's own MCP its first connection, with no
   change in behavior.
2. Send the turn's session proof with the platform's MCP tools the platform labelled for the
   turn's person (superseded by step 4).
3. Register the two tools for each application in `mcp_applications`.
4. Issue #78: send the envelope on every call made for the work; take the person only from the
   platform's answer to the turn start; let application tools run without a person; replace
   `requester_client()` with `platform_client()`; remove the per-tool labels, the refusals of a
   turn that serves nobody and the operations kept for a platform that had not removed private
   Secret entry.
5. In the same change as each step, update the
   [Security and access guide](../reference/security-model.md), the public API reference and the
   packaged skills. The security model states the write risk in section 2, application
   connections, delegation and turns that serve nobody.
6. Tests show that tokens, proofs and assertions never reach model context, tool results, history
   or logs; that no MCP session or catalog is shared between people; that a hosted call names its
   session and carries the delegation exactly when the turn serves a person; that a refused
   delegated call is not retried as the Agent; and that the runtime's housekeeping carries no
   delegation.
