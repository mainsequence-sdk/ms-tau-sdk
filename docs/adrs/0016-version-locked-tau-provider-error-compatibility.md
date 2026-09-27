# ADR 0016: Version-Locked Tau Provider Error Compatibility

Status: Accepted — implemented

Date: 2026-09-27

Amends:

- [ADR 0005: Authenticated local development mode](./0005-authenticated-local-development-mode.md)
  by making an imported provider failure inspectable in the private local structured log; and
- [ADR 0013: Guaranteed A2A Task terminalization and failure observability](./0013-guaranteed-a2a-task-terminalization-and-failure-observability.md)
  by retaining a safe provider classification while continuing to exclude traceback data from the
  public A2A failure payload.

## Context

`ms-tau-sdk` imports and pins `tau-ai`; it does not own or modify that project. In `tau-ai==0.4.2`,
the OpenAI-compatible adapter catches `httpx.HTTPError`, records the concrete exception type on
intermediate retry events, but constructs its final `ProviderErrorEvent` with only `str(exc)` and
the attempt count. Some HTTPX exceptions have an empty string representation. Tau also consumes
the retry events before its public assistant stream, so the SDK receives an empty `errorMessage`
and `{attempts: 3}` after the information needed to explain the failure has been destroyed.

The defect is reported upstream in
[huggingface/tau discussion 745](https://github.com/huggingface/tau/discussions/745). Waiting for an
upstream release would leave local Task failures opaque. Copying Tau's provider implementation
would transfer ownership of HTTP, credential, retry, SSE, and parser behavior into this SDK and is
not acceptable.

## Decision

The SDK installs one narrow compatibility patch only when it constructs an OpenAI-compatible
provider. The patch is locked to exactly `tau-ai==0.4.2` and has two interception points:

1. Tau's module-local `ProviderErrorEvent` constructor is wrapped. When it is invoked inside an
   active `httpx.HTTPError` handler, the wrapper reads that active exception, supplies a nonempty
   fallback message, and adds the concrete exception type, transport phase, exhausted-retry flag,
   diagnostic source, and one failure UID. It returns Tau's original Pydantic event type.
2. Tau's private `_stream` result is wrapped without reimplementing it. The wrapper observes Tau's
   own retry and terminal events, adds total provider duration, and attaches a bounded retry
   summary that excludes error strings, response bodies, headers, credentials, prompts, and tool
   payloads.

The original exception is also emitted once as `dependency.call.failed` through the SDK logging
pipeline. Private structured logs retain the exception type and bounded traceback locations, not
locals or credentials. The public Tau/A2A result receives the safe type/phase/attempt summary but
not the traceback. The same failure UID is propagated through model and turn lifecycle logs so Tau
Board groups the propagation as one human-readable incident; raw JSON remains secondary.

The patch does not edit site-packages, fork Tau, replace its HTTP client, reproduce its retry
algorithm, or alter providers other than the affected OpenAI-compatible adapter.

## Upgrade and removal gate

The exact dependency pin and a regression test are the compatibility gate. If the installed Tau
version differs from `0.4.2`, provider construction fails with an instruction to review the
upstream resolution instead of silently applying private assumptions to changed code.

When Tau releases a fix, upgrading requires all of the following in one change:

1. verify Tau's supported public stream returns a nonempty message, concrete HTTP exception type,
   attempt count, retry exhaustion, and sufficient duration evidence;
2. run the empty-message `ReadTimeout` regression against the candidate Tau version;
3. remove the constructor and stream wrappers rather than extending their version range;
4. keep the SDK failure-normalization and Tau Board rendering because they are SDK-owned consumer
   behavior; and
5. record the upstream release and removal in the changelog.

## Consequences

- Local operators see `ReadTimeout after 3 attempts`, the read phase, actual provider duration,
  and structured traceback frames instead of an empty `ProviderError`.
- A private Tau boundary is temporarily patched, but version drift fails visibly and the patch is
  small enough to delete as a unit.
- The historical failure that triggered this ADR cannot be reconstructed; only executions after
  this patch retain the missing evidence.
- Other Tau provider adapters are unchanged and require separate evidence before any compatibility
  treatment is considered.

