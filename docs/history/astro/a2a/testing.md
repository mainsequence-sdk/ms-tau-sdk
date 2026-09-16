# A2A Testing

Contract tests cover standard REST routing, JSON-RPC routing, task persistence,
stream framing, cancellation, and inline PDF validation.

Production smoke tests should:

1. Create a backend A2A task and session.
2. Send `message:send` and confirm the task reaches a terminal state.
3. Send `message:stream` and confirm only A2A task events are exposed.
4. Cancel an active task and confirm Tau cancellation and backend state agree.
5. Restart Astro and confirm the task/session can resume from backend Tau
   entries without a shared filesystem.

Raw Tau and provider events must never appear in public A2A responses.
