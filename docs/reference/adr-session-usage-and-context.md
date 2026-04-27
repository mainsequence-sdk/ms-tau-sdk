# ADR: Session Insights Endpoint

## Status

Accepted

## Context

Astro already persists local stream sessions and Pi session files, but it does not expose
session-level usage or context visibility over HTTP.

The frontend needs to show:

- cumulative token usage for a session
- the current context size
- how many tokens remain before automatic compaction triggers

Backend session insights are the source of truth for live usage displays.

Pi already has the necessary source data in the session file:

- assistant usage per turn
- tool calls and tool results
- compaction entries with `tokensBefore`
- the current branch context

## Decision

Astro will expose one read-only endpoint backed by the local Pi session file:

- `GET /api/chat/session-insights?sessionId=<runtime_session_id>`

`session-insights` returns one coherent snapshot with these sections:

- `session`
- `model`
- `usage`
- `context`
- `lastTurn`

The `context` section must include:

- `contextWindow`
- `compactionReserveTokens`
- `compactionThresholdTokens`
- `tokensRemainingBeforeCompaction`
- `tokensRemainingBeforeContextLimit`

Astro will compute usage/context from the Pi session file and local conversation snapshot, not from
the backend record.

Internally, Astro may still build usage and context with separate helpers, but the accepted public
HTTP contract is a single endpoint so the frontend gets one consistent snapshot.

For context calculations, Astro follows Pi's semantics:

- use the current branch context from the Pi session file
- if the session compacted and there has been no successful assistant turn afterward, return
  `status: "unknown_after_compaction"`
- otherwise estimate current context from the resolved message list

## Consequences

### Positive

- users can inspect session usage immediately without waiting for backend schema work
- context numbers align with Pi's session model more closely than a frontend-only estimate
- tokens-left-before-compaction becomes available to the UI
- one response keeps usage and context in sync for the same UI render

### Negative

- the active HTTP API is only as accurate as the local session files
- context is still an estimate when trailing messages exist after the last provider usage record
- the payload is larger than a narrowly scoped single-purpose endpoint

## Follow-up

- keep the computed usage/context snapshot mirrored to backend session insights after accepted
  checkpoint flushes
- extend stored model runtime limits if we later need provider-specific context windows instead of
  Pi defaults
- if provisional split endpoints exist during implementation, do not treat them as the stable public
  contract

## Tasks

- [x] Add an ADR for session usage/context visibility.
- [x] Add `GET /api/chat/session-insights`.
- [x] Return both `usage` and `context` in that endpoint.
- [x] Include `tokensRemainingBeforeCompaction` in the `context` response.
- [x] Mirror local usage/context snapshots back to backend session insights.
