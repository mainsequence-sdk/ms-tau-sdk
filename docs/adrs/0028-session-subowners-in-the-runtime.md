# ADR 0028: Session Subowners In The Runtime

Status: Proposed — not implemented

Date: 2026-11-08

Companion: tdag-django agents ADR-042, *Session Subowners For An Application's Own People*, which
adds the subowner to the platform's session. This ADR records what the runtime does with it.

Amends, on acceptance:

- [ADR 0019: Verified request identity and session ownership](./0019-verified-request-identity-and-session-ownership.md).
  The owner rule is unchanged; a session may also carry a subowner, and a declared check may
  refuse a turn whose baggage names another person.
- [ADR 0021: MCP connections on the person's identity](./0021-mcp-connections-on-the-persons-identity.md).
  A call to a declared application also names the turn's session in a reserved header.
- [ADR 0027: Custom provider credential hydrate endpoints](./0027-custom-provider-credential-hydrate-endpoints.md).
  A custom endpoint may resolve the person from the session's subowner.

## Context

An application that serves people the platform does not know owns their sessions with its
workload User. ADR-042 lets it name the person each session serves, the subowner, as an opaque
pair the platform indexes but never interprets. The runtime learns it from the bootstrap.

Today the runtime admits a request to a session by the owner rule of ADR 0019. For such an
application the owner is the application itself, so the runtime cannot tell one person's relay
from another's, stamps every turn's provenance with the application as actor, and calls the
application's tools with the Agent's own token and no statement of which session the call serves.
The application therefore cannot bind a tool call to a person (ms-tau-sdk analysis of 2026-11-08).

## Decisions

### 1. The runtime keeps the subowner

`AgentSession` gains `subowner: {issuer_uid, subject} | None`, read from the bootstrap and from
`get_session`. It is never written by the runtime: the platform sets it at creation.

### 2. Provenance names the subowner

The per-turn provenance stamp (`io.mainsequence.provenance`) gains `subownerIssuerUid` and
`subownerSubject` when the session has a subowner. The platform's history projection copies only
its known keys and ignores these; they stay in the entry for the application's own projections and
for local chat history, which shows them beside the actor.

### 3. Application calls name the session

Every call to a declared application, over MCP or `release_request`, carries
`X-MainSequence-Agent-Session-UID: <session uid>` when it is made inside a turn. The header is
reserved: project code cannot set or override it through `platform_client()` or
`requester_client()`, as it cannot set the other `X-MainSequence-*` headers today. Calls made
outside a turn carry no such header.

The header is the runtime's statement, on a channel the platform authenticated as the Agent's
workload. An application that trusts its own Agent's runtime, as it already does when it answers
that runtime's hydrate (ADR 0027), may accept it and resolve the person from the session: through
its own record of the session, or by reading the session's subowner from the platform as the
owner. The header is never a delegation and grants nothing on the platform.

### 4. A declared baggage check

`TAU_SUBOWNER_BAGGAGE_KEYS=<issuer key>,<subject key>` names the two baggage keys an application
uses for its people (ADR-0057 leaves the names to the application). When set, a request that
addresses a session with a subowner is refused with `403 subowner_mismatch` unless its baggage
names the same pair. A session without a subowner is not checked. Unset, nothing is checked, and
the runtime keeps giving baggage no meaning.

This is defense in depth against the application relaying one person's turn into another's
session. The owner rule stays the first check; this one runs after it.

### 5. What stays as it is

- The requester. A turn for a subowner serves nobody on the platform: `current_requester()` is
  `None`, delegated platform calls are refused, and application tokens are the Agent's own.
- Child sessions. The platform inherits the subowner from the parent, so the runtime sends nothing
  new when it delegates.
- Hydration. ADR 0027's custom endpoint still resolves by session uid; it may use the platform's
  subowner for that.
- Local mode. Sessions have no subowner.
- Logs. The subowner is bound to the turn's log context as `subowner_issuer_uid` and
  `subowner_subject`, opaque ids like the baggage keys the runtime already records.

## Acceptance criteria

- A bootstrap with `session.subowner` populates the runtime session; one without leaves it `None`.
- A turn on a session with a subowner writes a provenance entry carrying the pair; the local chat
  history projection shows it; a session without one writes today's stamp.
- A tool call to a declared application inside a turn carries the session header; a call outside
  a turn does not; project code that sets the header has its value dropped.
- With the keys declared, a request whose baggage names another pair is refused with
  `subowner_mismatch`, and one with matching baggage is admitted; with the keys unset, the same
  request is admitted.
- No token, assertion or lease proof changes; the existing identity tests pass unchanged.

## Rejected

- **Deriving the subowner from baggage in the runtime.** The platform record is the fact; baggage
  is a claim. The runtime binds nothing from baggage and only checks consistency when told how.
- **A platform-minted session claim on application tokens.** It needs a platform change to the
  requester-binding path and the exact claim set; the owner chose the platform's session record
  and a runtime header instead.
- **Carrying the subowner itself in the header.** The session uid is enough: the application
  resolves the person from it, and one value is less to validate.
