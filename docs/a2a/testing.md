# A2A Testing Contract

Astro A2A changes must be tested at the boundary they can break.

## Required Test Layers

1. Wire contract tests

These are fast unit tests for the public A2A surface.

- `POST /api/a2a/v1/message:send` is accepted.
- `POST /api/a2a/v1/message:stream` is accepted.
- task, subscribe, push config, extended agent card, and JSON-RPC routes are accepted.
- removed Astro-specific routes stay removed:
  - `/api/a2a/chat`
  - `/api/a2a/sessions/{session_uid}/runtime`
  - `/api/a2a/sessions/{session_uid}/runtime/chat`
- `message.contextId` is the backend `AgentSession.uid`.
- `message.messageId` is required and drives idempotency.

Current coverage:

- `tests/a2a-standard-routing.test.ts`
- `tests/a2a-standard-fixtures.test.ts`

2. HTTP lifecycle tests

These verify that A2A behaves like an RPC boundary, not detached UI chat.

- If the request socket aborts, the runtime turn receives a cancellation signal.
- If the response closes before ending, the runtime turn receives a cancellation signal.
- A normal response close after `writableEnded` does not trigger cancellation.
- Disposed abort listeners do not fire later.
- A closed/destroyed/aborted response is not written to.

Current coverage:

- `tests/http-client-abort.test.ts`

3. Output contract tests

These protect the caller-facing response shape.

- `omitReasoning` suppresses reasoning, tool traces, and text deltas.
- strict JSON/dictionary responses are validated.
- invalid strict JSON is repaired or rejected.
- final A2A response contains the answer, not raw Pi internals.

Current coverage:

- `tests/a2a-output.test.ts`

4. Runtime option tests

These protect behavior toggles that affect execution lifetime.

- runtime turn timeout is disabled unless requested.
- timeout values normalize consistently across request/context aliases.
- warm-runner startup/RPC/turn timeout defaults are bounded.
- a warm runner that accepts a prompt but never emits completion has a server-side timeout path.

Current coverage:

- `tests/a2a-runtime-options.test.ts`
- `tests/warm-runner-config.test.ts`

## Missing Required Integration Tests

The next test gap is a real A2A lifecycle harness with a fake backend and fake Pi RPC runner.
Do not consider the A2A surface fully protected until this exists.

It must verify:

- `message:send` fetches/hydrates the backend session once for a cold request.
- a cold request starts one warm runner, dispatches one prompt, receives one assistant answer,
  returns one A2A `message`, and logs `warm_runner_dispatch_completed`.
- a second message with the same `contextId` reuses the warm runner and does not repeat cold
  startup.
- retrying the same `(contextId, messageId)` returns the same in-flight/completed result.
- reusing the same `(contextId, messageId)` with different content returns conflict.
- a fake Pi runner that never sends `message_end` fails with a bounded timeout and a useful
  error instead of hanging silently.

### Required Cancel/Overlap Stress Scenario

This is the minimum stress test for same-session A2A lifecycle safety:

1. Send request A to `POST /api/a2a/v1/message:send` with `contextId = <same AgentSession.uid>`.
2. 500ms later, abort request A from the HTTP client before the fake Pi runner answers.
3. Immediately send request B with the same `contextId` and a different `messageId`.
4. Assert request B starts after request A's warm runner abort acknowledgement, without waiting for
   request A's Pi process exit, warm-runner timeout, or HTTP request timeout.
5. Assert request B returns one valid A2A `message` or `task` response.
6. Send request C with the same `contextId` after B completes.
7. Assert request C is not blocked by stale queue/current-turn state from either A or B.

Required assertions:

- request A cancellation sends a Pi RPC `abort` to the active warm runner.
- request B reaches warm runner dispatch promptly after A's abort is acknowledged.
- request B and request C reuse the same fake warm runner when abort succeeds.
- the warm runner is not stopped on successful HTTP cancel; it is stopped only when abort fails or
  times out.
- at most one active turn mutates the session at a time.
- no public response contains raw Pi events, reasoning chunks, tool traces, or Astro stream chunks.
- logs include correlated lifecycle events for queue, cancellation, dispatch, completion, and
  timeout using the same `request_id` and `agent_session_uid`.
- if the fake Pi runner accepts a prompt and never emits completion, Astro returns a bounded
  timeout error and the next same-session request can still run.

The fake harness should not call the real backend, real ngrok URL, real Main Sequence CLI,
or real provider. It should inject:

- backend session payload
- model binding
- capability result
- fake warm Pi RPC process events
- fake assistant `message_update` / `message_end` events

## Live Container Workflow

The fake harness protects deterministic release behavior. When debugging a deployed or locally
running container, use the live workflow to see the real A2A request/response flow:

```bash
node scripts/stress_a2a_live.mjs \
  --base-url http://127.0.0.1:8787 \
  --agent-session-uid 0b2701a1-e777-4cfe-8437-b94025f00069 \
  --turns 10 \
  --cancel-after-ms 500 \
  --timeout-turn-ms 1000
```

The live workflow prints:

- each turn number and mode
- every request body
- every client-side cancel or timeout result
- every full A2A response body
- final status/duration summary

It intentionally uses the real running endpoint and real auth resolution. It is for reproducing
container behavior and seeing actual responses, not for replacing deterministic CI tests.

## Rule

Any future A2A transport change must update or add tests in the layer it changes. Source-text
assertions can remain as guardrails, but they are not a substitute for route, lifecycle, output,
and integration behavior tests.
