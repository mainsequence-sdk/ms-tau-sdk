# ADR 34: A2A Output Contracts, Reasoning Suppression, and Strict JSON

Status: Accepted
Date: 2026-06-15
Implementation Status: Implemented for the legacy Astro A2A SSE path; non-streaming JSON transport decision recorded

## Context

Astro's legacy A2A chat endpoint is now a real runtime execution path for agent-to-agent
communication. ADR 33 made same-session A2A execution faster by introducing cached preflight and
warm Pi RPC runners, but it did not change the outbound response contract.

Today the A2A response contract has three gaps:

- `response_format` is only prompt guidance. `buildA2ASystemInstruction()` injects it as text such
  as `Required response format: ...`, but Astro does not validate the assistant response.
- Pi `thinking_*` events are forwarded directly as `reasoning-start`, `reasoning-delta`, and
  `reasoning-end`. `shouldSuppressClientChunk()` always returns `false`.
- A2A text is streamed immediately as `text-delta`, so Astro cannot guarantee valid JSON before the
  caller has already received possibly-invalid prose.

This means a caller can ask for JSON and still receive natural language, markdown fences, partial
JSON, or reasoning boundary events in the SSE stream.

## Problem

A2A callers need machine-facing response guarantees for common automation flows:

- call another agent and suppress reasoning noise from the response stream
- require the final response text to be valid JSON
- distinguish "the model produced invalid JSON" from transport/runtime failure
- continue using legacy Astro A2A SSE when streaming is desired
- optionally use a non-streaming JSON transport when the caller wants a normal HTTP JSON response

Prompt-only instructions are not enough. A model can ignore instructions, produce prose before JSON,
or produce invalid JSON. If Astro has already streamed those bytes to the caller, the caller cannot
be guaranteed a clean JSON response.

## Decision

Astro will add explicit A2A output options and runtime-enforced strict JSON behavior.

Supported request options:

```json
{
  "message": "Return the status as JSON.",
  "omit_reasoning": true,
  "json_repair": {
    "attempts": 3
  },
  "response_format": {
    "type": "json_object",
    "strict": true
  }
}
```

Equivalent camelCase aliases are accepted:

```json
{
  "message": "Return the status as JSON.",
  "omitReasoning": true,
  "jsonRepair": {
    "attempts": 3
  },
  "responseFormat": {
    "type": "json_object",
    "strict": true
  }
}
```

### Reasoning Suppression

`omit_reasoning` / `omitReasoning` is a per-request A2A output option.

When enabled, Astro must not send these chunks to the A2A caller:

- `reasoning-start`
- `reasoning-delta`
- `reasoning-end`

This option controls outbound stream shape only. It does not change model reasoning effort, model
cost, or Pi's internal thinking behavior. Model reasoning effort remains controlled by session model
binding `runConfig.reasoning_effort`.

Strict JSON response mode defaults to the same outbound reasoning suppression unless the caller
explicitly sets `omit_reasoning: false`.

Reasoning suppression must not rely on prompt text. It must be enforced in Astro's stream writer or
assistant event handling.

### Strict JSON Response Mode

Astro will interpret these `response_format` forms as strict JSON:

```json
"json"
```

```json
{
  "type": "json_object",
  "strict": true
}
```

```json
{
  "type": "json_schema",
  "strict": true,
  "schema": {}
}
```

For strict JSON over standard A2A `message:send`, Astro must buffer assistant text instead of
forwarding raw text or stream chunks immediately.

At assistant completion:

- parse the accumulated assistant text as JSON
- for `json_object`, require the parsed value to be a JSON object, not an array or primitive
- for `json_schema`, validate against the schema if a validator is available in the runtime
- if valid, return one canonical JSON response
- if invalid, run the configured JSON repair loop before failing
- if still invalid after all repair attempts, emit an error event with
  `error_code: "a2a_invalid_json_response"` and do not emit invalid assistant text

This makes strict output behavior deterministic:

- non-strict mode may return normal assistant text
- strict JSON mode returns no assistant content until validation succeeds
- strict JSON mode returns either valid JSON data, repaired valid JSON data, or a structured error

### JSON Repair

Strict JSON mode includes an automatic repair loop.

Default behavior:

```json
{
  "json_repair": {
    "attempts": 3
  }
}
```

CamelCase alias:

```json
{
  "jsonRepair": {
    "attempts": 3
  }
}
```

Rules:

- default repair attempts is `3`
- `attempts: 0` disables repair and hard-fails on the first validation error
- repair attempts are per request
- repair must receive the original invalid text, the validation error, and the required response
  format
- repair prompt must ask for only corrected JSON, with no prose or markdown fences
- each repair output must be validated exactly like the original output
- Astro must not emit the original invalid text or intermediate failed repair text
- Astro should log repair attempts with attempt number and validation failure reason
- if repair succeeds, Astro returns the repaired canonical JSON response
- if all attempts fail, Astro emits `a2a_invalid_json_response`

Repair is allowed to change syntax and shape to satisfy the declared format. It must not be treated
as proof that the model response is semantically correct.

### Transport Boundary

The active non-streaming A2A transport is `POST /api/a2a/v1/message:send`. Strict JSON guarantees
the returned A2A `Message` part is valid JSON data when validation succeeds.

The transport returns a JSON object with either an A2A `message`/`task` result or an A2A error. A
strict JSON direct-message response should contain the canonical JSON value in a `data` part, for
example:

```json
{
  "message": {
    "role": "ROLE_AGENT",
    "messageId": "msg-agent-1",
    "contextId": "agent-session-uid",
    "parts": [
      {
        "data": {}
      }
    ]
  }
}
```

If validation and repair fail, Astro returns an A2A error instead of invalid assistant text:

```json
{
  "error": {
    "code": -32000,
    "message": "The agent did not produce valid JSON after repair attempts.",
    "data": {
      "code": "a2a_invalid_json_response"
    }
  }
}
```

`POST /api/a2a/v1/message:stream` may expose task-state SSE, but it must not expose raw Pi
reasoning, tool traces, or invalid strict JSON text.

## Per-request vs Durable State

`omit_reasoning` and strict JSON buffering are per-request output controls.

Astro already persists `responseFormat` in the durable A2A envelope. That is useful for preserving
caller intent across session state, but strict runtime behavior must be resolved from the current
request first so one JSON-constrained call does not accidentally force every later A2A turn into
buffered JSON.

Resolution order:

1. current request output options
2. current request `response_format`
3. durable A2A envelope `responseFormat` only when the request does not specify a response format

The resolved output options should be stored on `RequestContext`, not inferred repeatedly from
serialized metadata.

## Error Contract

Invalid strict JSON output must emit a stream error:

```json
{
  "type": "error",
  "error": "The A2A response was not valid JSON after repair attempts.",
  "error_source": "runtime",
  "error_code": "a2a_invalid_json_response",
  "error_detail": "...last parser or schema validation detail...",
  "forensics": {
    "json_repair_attempts": 3
  }
}
```

Astro must not send partial invalid text or failed repair text before this error.

If the provider/runtime itself fails, existing provider/runtime error behavior remains unchanged.

## Non-goals

This ADR does not:

- change model reasoning effort or provider reasoning controls
- remove reasoning from stored checkpoint metadata globally
- make every A2A response JSON by default
- replace legacy Astro A2A SSE
- implement schema validation if no JSON schema validator is present
- guarantee semantic correctness of model-produced JSON beyond syntactic/schema validity

## Consequences

### Benefits

- A2A callers can request clean streams without reasoning events.
- Strict JSON mode becomes enforceable instead of prompt-only.
- Callers get a deterministic success/error contract for JSON automation.
- SSE remains available for streaming cases.
- Future non-streaming JSON transport can reuse the same validation machinery.

### Costs

- Strict JSON mode delays assistant text until completion.
- Strict JSON mode cannot show partial text progress.
- JSON repair can add extra model calls and latency.
- Runtime code must track buffered assistant text per request.
- Error handling must distinguish invalid model output from provider/runtime failure.
- Tests must cover both cold `pi --mode json` and warm `pi --mode rpc` event paths.

## Implementation Tasks

- [x] Add `A2AOutputOptions` normalization with snake_case and camelCase aliases.
- [x] Resolve `omit_reasoning` / `omitReasoning` as a per-request option.
- [x] Resolve strict JSON mode from `response_format` / `responseFormat`.
- [x] Resolve JSON repair attempts from `json_repair` / `jsonRepair`, defaulting to 3.
- [x] Add `RequestContext.a2aOutputOptions`.
- [x] Suppress outbound reasoning chunks for A2A requests when `omitReasoning` is true.
- [x] Keep model `reasoning_effort` behavior unchanged.
- [x] Add strict JSON buffering for assistant text chunks.
- [x] Prevent `text-start`, `text-delta`, and `text-end` from reaching the caller before strict
      JSON validation succeeds.
- [x] Validate buffered assistant text at completion.
- [x] Add JSON repair loop for invalid strict JSON output.
- [x] Validate every repaired output before emitting it.
- [x] Canonicalize and return one JSON response when validation succeeds.
- [x] Emit `a2a_invalid_json_response` when validation and all repair attempts fail.
- [x] Ensure strict JSON invalid output does not persist as normal assistant text in the
      conversation store.
- [x] Add focused unit tests for output option resolution, strict JSON validation, and repair prompt
      construction.
- [x] Add tests for reasoning suppression in A2A output.
- [x] Add tests for strict `json_object` success.
- [x] Add tests for strict `json_object` invalid prose failure.
- [x] Add tests for JSON repair success after invalid first output.
- [x] Add tests for JSON repair exhaustion producing `a2a_invalid_json_response`.
- [x] Add tests for `json_repair.attempts: 0` hard-fail behavior.
- [x] Add tests proving non-strict A2A output behavior remains unchanged.
- [x] Add tests covering warm RPC runner output and cold Pi output.
- [x] Document request examples in the A2A interface docs.
- [x] Use `POST /api/a2a/v1/message:send` for HTTP JSON transport.

## Related

- [`adr-25-production-a2a-discovery-and-runtime-access.md`](./adr-25-production-a2a-discovery-and-runtime-access.md)
- [`adr-28-durable-a2a-session-envelope.md`](./adr-28-durable-a2a-session-envelope.md)
- [`adr-33-warm-a2a-session-runtime.md`](./adr-33-warm-a2a-session-runtime.md)
- [`adr-checkpoint-reasoning-annotations.md`](./adr-checkpoint-reasoning-annotations.md)
