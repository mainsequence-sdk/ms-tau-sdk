# ADR: Keep A2A Envelope As Durable Session State

## Status

Accepted

## Context

Astro's current A2A path is intentionally lightweight:

- the inbound A2A request is normalized into the normal stream pipeline
- the bounded request is passed through the existing `messages` interface
- the runtime prompt is still built around the normal `user` / `assistant` chat model

That compatibility has been useful, but it leaves an architectural gap.

Today, the durable local session model primarily remembers:

- session identity
- cached session metadata
- flattened user/assistant text

The richer A2A envelope is mostly transient:

- caller agent identity
- response-format requirements
- delegated handle/session linkage
- original structured A2A request shape

Those fields may exist on the inbound request and in prompt injection, but they are not currently
treated as first-class durable session state in Astro's local conversation model.

At the same time, introducing a brand-new conversational role such as `agent` would be a much
larger change than this problem requires. The current runtime, local history projection, session
insights, and UI contracts are all strongly shaped around:

- `user`
- `assistant`
- special runtime artifacts such as tool results

Changing that role model now would create broad protocol and persistence churn.

## Problem

A2A is conceptually communication between sessions, but Astro currently models it too much like a
decorated human chat turn.

That creates three concrete weaknesses:

1. the durable session record loses the structured A2A contract and mostly keeps only flattened
   text
2. resumed or retried A2A-backed sessions depend too heavily on cached metadata and prompt
   reinjection instead of durable envelope state
3. the system has no first-class local representation of the machine-facing A2A relationship even
   though that relationship shapes runtime behavior

The result is brittle continuity. The session may still have the right backend identity, but Astro
does not retain the full A2A envelope as durable local session state.

## Decision

Astro will keep the runtime conversational roles as `user` / `assistant` for now, while promoting
the A2A envelope to first-class durable session state.

The bounded request should continue to travel through the existing message pipeline as a `user`
turn for compatibility with the current Pi/history model.

Astro must not introduce a new first-class `agent` chat role as part of this change.

Instead, Astro should persist and hydrate a durable A2A session envelope alongside the existing
session metadata/checkpoint state.

The important distinction is provenance, not role:

- the inbound bounded request still enters the runtime as a `user` turn
- Astro should durably mark that turn as **agent-originated**
- caller/response/linkage semantics should live in the durable A2A envelope, not in a new chat role

## Required Durable Envelope

For A2A-backed sessions, the durable envelope should preserve enough structured state to explain
and resume the inter-session relationship.

That envelope should include, at minimum:

- whether the session was entered through A2A
- whether the bounded `user` turn was agent-originated
- caller agent identity
- caller metadata when supplied
- required response format
- backend delegated handle/session linkage used for retry/reuse
- the normalized structured A2A request shape, or a bounded persisted representation of it

This is an Astro-side consumer concern.

Backend session allocation, delegated handle semantics, and runtime access resolution remain owned
by the backend control plane and its ADRs.

## Minimal Backend Contract Needed By Astro

Astro should not guess whether a delegated session is A2A-backed when the backend already knows
that lineage.

To durably identify that a bounded `user` turn came from another agent, Astro needs the backend
session contract to expose the delegated provenance that already exists at allocation time.

The minimum useful backend-facing data is:

- whether the backend session was allocated for delegated A2A communication
- the stable delegated `handle_unique_id`
- the caller backend `AgentSession.uid`
- the target backend `Agent.uid`

Preferred additional backend-facing data:

- caller agent identity when the backend already has it in canonical form
- a stable backend-owned A2A envelope object in the session serializer or session metadata

Astro can then combine:

- backend-owned delegated provenance
- the current inbound A2A request fields such as caller metadata and response format

to durably mark the bounded `user` turn as agent-originated without introducing a new chat role.

This does **not** require the backend to own the entire A2A request transcript.

It only requires the backend session contract to expose enough delegated lineage for Astro to
distinguish:

- human-originated `user` turns
- agent-originated `user` turns

## Required Runtime Behavior

### 1. Preserve compatibility with the current chat-role model

Astro should continue to pass the bounded A2A request through the existing message pipeline as a
`user` turn.

The runtime should continue to emit assistant output as `assistant`.

This ADR does not authorize a new wire role such as `agent`.

Where the implementation needs to distinguish human-originated from agent-originated input, it
should do so through durable A2A envelope fields or message provenance metadata rather than by
changing the conversation role itself.

### 2. Materialize the A2A envelope before Pi launch

When Astro accepts a real A2A `message:send` request, it should materialize the normalized A2A
envelope into durable local session state before Pi launch instead of keeping it only in transient
request context or prompt text.

### 3. Hydrate the A2A envelope on attach and resume

When Astro reattaches to an existing backend-owned session, the local durable A2A envelope should
be restored together with the rest of the session metadata/checkpoint state.

Resumed A2A-backed sessions should not depend solely on one-shot reinjection from the newest
request when the envelope was already known for that session lineage.

### 4. Keep prompt injection as a projection, not the only source of truth

Prompt injection for caller identity and response format may remain useful, but it should be a
projection of durable envelope state, not the only place where the A2A relationship exists.

### 5. Preserve delegated linkage explicitly

Astro should retain the delegated linkage returned by the backend control plane, including the
stable delegated handle identity used for retry/reuse, rather than treating that linkage as
disposable request scaffolding.

### 6. Mark user-turn provenance explicitly

For A2A-backed requests, Astro should persist explicit provenance that the inbound bounded `user`
turn was agent-originated.

That provenance should be durable enough to survive:

- attach/resume
- checkpoint restore
- local history reconstruction

The provenance marker should be additive metadata, not a replacement for the existing
`user` / `assistant` role model.

## Non-goals

This ADR does not:

- introduce a new `agent` conversational role
- redesign Pi's chat-role model
- redefine backend delegated-session allocation semantics
- replace backend-owned session identity with local Astro identity
- require Astro to store a second canonical transcript separate from the existing session/history
  model

## Consequences

### Benefits

- A2A state becomes durable without forcing a protocol-wide role migration
- resumed and retried A2A sessions can rely on structured local envelope state instead of only
  flattened text plus cached metadata
- the system gains a first-class local record of machine-facing caller/response requirements
- the change stays compatible with the current Pi/history model

### Costs

- Astro must persist and hydrate additional session-scoped structure
- session metadata/checkpoint design becomes slightly richer
- some current prompt-only A2A assumptions should be refactored into explicit durable state over
  time

## Implementation Tasks

- [x] Persist a durable A2A envelope in Astro session-scoped metadata/checkpoint state.
- [x] Include explicit provenance that a bounded `user` turn was agent-originated.
- [x] Preserve caller agent identity, caller metadata, response format, and delegated linkage in
      that envelope.
- [x] Hydrate the A2A envelope on attach, resume, and checkpoint restore.
- [x] Keep prompt injection as a projection of the durable A2A envelope rather than the sole source
      of truth.
- [x] Ensure local history reconstruction can distinguish human-originated versus agent-originated
      `user` turns without introducing a new `agent` role.
- [x] Consume backend-exposed delegated provenance fields when present instead of inferring A2A
      lineage only from transient request context.
- [x] Update the Astro-facing A2A docs to describe the durable provenance rule for agent-originated
      `user` turns.

## Amendment 2026-09-02: Tau implementation

The Pi harness met the implementation tasks above through its durable A2A envelope. The Tau harness
persisted nothing per turn: `/api/chat` and the A2A routes both reached
`manager.prompt(session_uid, text)` and stored byte-identical `UserMessage` entries, so backend
history could not tell a human turn from an agent turn on a Tau session.

Tau now stamps every bounded `user` turn with a tau-native `CustomEntry`, appended inside the turn
immediately before the user message:

- namespace `io.mainsequence.provenance`
- data `{"channel": "chat" | "a2a" | "responses", "origin": "user" | "agent"}`
- `channel` is derived from the route that received the request; `origin` follows the channel
  (`chat` is human-facing, the A2A and sessionless routes are agent-facing). Nothing from the
  request body is trusted for the stamp. Caller identity fields arrive in a later phase from
  gateway-verified headers only.

Why a custom entry: it is already part of the canonical Tau entry contract, the Tau session state
keeps custom entries out of the model context, it reloads as a first-class entry, and it rides the
turn's commit batch, so the send path gains no request. The backend (tdag-django ADR-008,
amendment of the same date) attaches the most recent stamp on the active branch to the next
projected `user` message as message-level `provenance`. The `user` / `assistant` role model stays
intact, as decided above.

Implementation: `astro.runtime.provenance`, `ActiveSessionRuntime.prompt(provenance=...)`,
`SessionRuntimeManager.prompt(provenance=...)`, and the chat and A2A routes.

## Amendment 2026-09-02 (2): Gateway-verified caller identity

The Tau implementation above records the channel a turn arrived on. This amendment records who
sent it, using identity that the platform verified, so a name and later an avatar can be drawn on
the turn. It implements the runtime side of tdag-django
`docs/platform/adr/adr-0043-caller-identity-in-coding-agent-runtime-access-tokens.md` as amended
on 2026-09-02 to a coordinated hard cutover: that ADR is Proposed, and this amendment takes effect
only through the cutover it describes. There is no compatibility mode and no mixed-version
operation.

### Inputs

The coding-agent gateway sets these request headers from Django's validation verdict and blanks
any client-supplied copies, so inside the runtime they are trustworthy. After the cutover every
validated request carries `X-Caller-Kind`, because Django rejects tokens without `caller_kind`.

| Header | Meaning |
|---|---|
| `X-User-UID` | responsible User for the request (unchanged) |
| `X-Username` | that User's username (unchanged) |
| `X-Caller-Kind` | `user` when the token was minted for a user request or by the explicit Django admin helper, `agent` when it was minted for a runtime credential; mandatory |
| `X-Caller-Agent-UID` | calling Agent UID; present if and only if `X-Caller-Kind` is `agent` |
| `X-Caller-Coding-Agent-Service-UID` | calling CodingAgentService UID; present if and only if `X-Caller-Kind` is `agent` |
| `X-Caller-Agent-Session-UID` | the authorized delegation-parent AgentSession UID; present only for a cross-Agent delegated target (a same-Agent session with a parent carries none); the bearer credential authenticates the calling Agent, not a specific request session, so treat it as "authorized delegation parent" |

Nothing else is an identity source. A2A `message.metadata`, request bodies, the Pi
`context.a2a.caller` envelope, and any `caller_*` field a client sends are ignored for identity;
when they disagree with headers, the headers win.

### Validation and rejection

Every protected message route validates the headers before any session work and before any
entry is persisted:

- `X-Caller-Kind` must be present and exactly `user` or `agent`.
- For `user`: `X-User-UID` must be present and a canonical lowercase UUID
  (`str(uuid.UUID(v)) == v`); the Agent-specific caller headers must be absent or empty.
- For `agent`: `X-Caller-Agent-UID` and `X-Caller-Coding-Agent-Service-UID` must be present and
  canonical; `X-Caller-Agent-Session-UID`, when present, must be canonical.
- `X-Username`, when present, is bounded to 255 characters.

Any violation rejects the request with HTTP 403 and body
`{"detail": "Missing or invalid caller identity headers.", "code": "runtime_caller_identity_invalid"}`.
The chat route returns that JSON response instead of opening the event stream; the A2A REST
routes return it as their HTTP response; the JSON-RPC handler returns a JSON-RPC error carrying
the same `code`. No turn is started, so nothing is stamped and nothing is persisted. The former
route-derived `origin` fallback of the first amendment does not apply after the cutover.

Protected message routes are the ones that persist a user turn: `POST /api/chat`, the A2A
session routes (`message:send`, `message:stream`, the task turn, and the JSON-RPC message
methods). `/health`, `/ready`, `/version`, `GET /api/chat`, and `/api/chat/session-model` are
not message routes and are unchanged. The sessionless `/api/agents/{uid}/responses` routes persist
no entries and are outside this amendment.

### Stamp

`astro.runtime.provenance.turn_provenance_from_request(channel, headers)` builds the custom-entry
data (namespace `io.mainsequence.provenance`, appended inside the turn before the user message as
in the first amendment). It is called only after validation succeeded:

| Key | Value |
|---|---|
| `channel` | the route, as today: `chat` or `a2a` |
| `origin` | `agent` if and only if `X-Caller-Kind` is `agent`; `user` if and only if it is `user`; never derived from the route after the cutover |
| `actorKind` | `X-Caller-Kind` |
| `actorUid` | `X-Caller-Agent-UID` for `agent`; `X-User-UID` for `user` |
| `actorName` | `X-Username`, for `user` only (agent names are resolved by the backend at projection time) |
| `callerAgentSessionUid` | `X-Caller-Agent-Session-UID` (authorized delegation parent), omitted when absent |

`targetAgentUid` is not stamped; the backend projection fills it from the session's Agent.
`build_turn_provenance(channel)` remains only as the unit that maps a route to its `channel`; it
no longer decides `origin` on protected routes.

### Where

- `astro/runtime/provenance.py`: `validate_caller_identity(headers)` returning the parsed identity
  or raising the structured 403, and `turn_provenance_from_request(channel, headers)`.
- `astro/api/chat.py` (`chat`), `astro/api/a2a.py` (`message_send`, `message_stream`, the JSON-RPC
  handler, and the task turn), all of which already receive the FastAPI `Request`: they validate
  first, then pass the built provenance down. `_collect_turn` receives the built provenance from
  its caller.
- `astro/logging.py`: the request middleware binds `caller_kind` and `caller_agent_uid` next to
  `user_uid` (UIDs only; ADR-38 sinks unchanged), and the rejection path emits
  `turn.caller_identity_rejected` with the failing header names and no values.

### Trust boundary

Public runtime traffic reaches the runtime only through the gateway (ADR-25: the runtime URL is
the brokered public URL). A request that bypasses the gateway carries no `X-Caller-Kind` and is
rejected before persistence; it is never stamped by route.

### Cutover

This behavior ships in an astro-tau release that is deployed only as step 4 of the ADR-0043
cutover, after Django and both gateways and under stopped traffic, and is rolled back with them.
Deployed alone it rejects every message request, because no verdict would carry
`X-Caller-Kind`; that is by design, not a fallback to be added. The hot path gains nothing: the
middleware already parses identity headers per request, validation is string checks, and the
extra keys are a few bytes in the existing custom entry.

### Tests

- header-to-stamp mapping for `user` and `agent`, including `origin` following `X-Caller-Kind` on
  an A2A route with a `user` caller;
- rejection before persistence for each violation: absent `X-Caller-Kind`, unknown kind, `user`
  with Agent caller headers, `agent` without its required headers, non-canonical UUIDs; assert no
  entry was appended and the exact 403 body, on the chat route and on each A2A route;
- an A2A body or metadata carrying an agent identity does not change the stamp;
- the logging middleware binds `caller_kind` and `caller_agent_uid` and emits the rejection event;
- non-message routes still answer without caller headers.

### Implementation Tasks

- [ ] `validate_caller_identity` and `turn_provenance_from_request`, with the structured 403.
- [ ] Chat and A2A message routes validate before session work; `_collect_turn` takes the built
      provenance; JSON-RPC maps the rejection to a JSON-RPC error.
- [ ] Request log context binds `caller_kind` and `caller_agent_uid`; rejection event.
- [ ] Tests listed above.
- [ ] Release note marking the image as ADR-0043 cutover-bound.

## Related

- [`adr-25-production-a2a-discovery-and-runtime-access.md`](./adr-25-production-a2a-discovery-and-runtime-access.md)
- [`adr-27-backend-only-session-initiation.md`](./adr-27-backend-only-session-initiation.md)
