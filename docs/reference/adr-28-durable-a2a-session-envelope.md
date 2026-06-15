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

When Astro accepts a real `POST /api/a2a/chat` request, it should materialize the normalized A2A
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

## Related

- [`adr-25-production-a2a-discovery-and-runtime-access.md`](./adr-25-production-a2a-discovery-and-runtime-access.md)
- [`adr-27-backend-only-session-initiation.md`](./adr-27-backend-only-session-initiation.md)
