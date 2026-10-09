# ADR 0022: Time Limits and Failures of MCP Application Tool Calls

Status: Accepted — implemented

Date: 2026-10-09

Issue: [#84](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/84)

Amends:

- [ADR 0021: MCP connections on the person's identity](./0021-mcp-connections-on-the-persons-identity.md),
  section 4, by reading an application's tool list in the session that makes a call, and by
  deciding how long a call may run, how its failures are reported and when it is retried.

## Context

In 2.0.8 a call to an application's tool (`<name>__call_tool`) has four problems.

1. **A failed request can leave the call waiting.** When the application, or the gateway in front
   of it, answers the tool call with an HTTP error status (for example 403, 502 or 503), the MCP
   library ends the whole connection. The SDK's connection client then fails the calls still queued
   on that connection, but not the call already sent. That call waits until the turn's limit
   (`MAINSEQUENCE_TAU_TURN_TIMEOUT_SECONDS`, 15 minutes by default) or a cancellation ends it, and
   the connection's own time limit never applies. The Main Sequence MCP uses the same client and
   has the same defect.
2. **One time limit for every tool.** Every application tool call is cut off after
   `MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS` (60 seconds by default), a setting meant for the
   runtime's calls to the platform. It is applied twice: as the MCP session's wait for an answer
   and as the HTTP client's read wait. A tool that legitimately needs longer cannot run, and fast
   and slow tools get the same limit.
3. **Almost every failure reads "could not be reached".** Apart from an ended access and a platform
   refusal, every failure (a time-out, a server error, a refusal by the application, a protocol
   error) reaches the model as "The <name> application could not be reached." The model then tells
   the person the application is down when it only took too long.
4. **Nothing is retried,** not even a failure that happened before the tool call was sent.

The incident in #84 is problems 2 and 3: a read tool took longer than 60 seconds, the SDK gave up
(the gateway logged 499 after 59.9 seconds), and the Agent told the person the application was
unavailable.

## Decision

### 1. A call in progress always ends

When an MCP connection fails, every call on it ends with that failure: the calls still queued and
the call already sent. An HTTP error status on any request ends the call with an error that carries
the status. No call waits past its own time limit. This applies to every MCP connection the runtime
opens, including the Main Sequence MCP.

### 2. Each tool declares its own time limit

The application declares how long one of its tools may run, in the tool's `_meta`, under
`mainsequence.ai/timeout-seconds/v1`, as a positive number of seconds. The protocol has no field for
it; `_meta` is its place for extensions, and the key has the form of the SDK's other
`mainsequence.ai/.../v1` keys. The tool's author sets it where the tool is defined. With the MCP
Python SDK that is the tool decorator:

```python
@mcp.tool(meta={"mainsequence.ai/timeout-seconds/v1": 300})
async def run_query(...): ...
```

`<name>__call_tool` reads the application's tool list in the session it opens for the call, before
it sends the call, and waits for the tool's answer for the tool's declared time or, when the tool
declares none, `TAU_MCP_TOOL_TIMEOUT_SECONDS` (60 seconds by default). The default's name starts
with `TAU_`, like the tool composition settings of
[ADR 0011](./0011-independent-base-tool-and-main-sequence-mcp-exclusion.md), so that an Agent can
set it in its workflow file's `harness_agent.spec.env_vars`, where the platform reserves
`MAINSEQUENCE_` names. A missing, zero, negative, infinite or non-numeric value means the default;
an invalid value is logged with the application and tool names. A tool that is not in the list is
called with the default, and the application answers whether it exists. The turn's own limit still
applies: a call that outlives its turn ends with the turn.

The limit is the MCP session's wait for that one request. The HTTP client's read wait on an
application connection is the turn's limit, so it never cuts a call short.
`MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS` keeps bounding the runtime's calls to the platform
and the steps that open an application session: the connection, the MCP handshake and the tool
list.

### 3. Failures are reported in plain words

The tool result tells the model what happened, without internals:

| What happened | What the model reads |
| --- | --- |
| The tool did not answer within its limit | The `<name>` tool `<tool>` did not answer within `<N>` seconds. |
| Listing the tools did not finish within its limit | The `<name>` application did not answer within `<N>` seconds. |
| The application or gateway reports a timeout (408, 504) | The `<name>` application or its gateway reported a timeout (`<status>`). |
| Another server or gateway error status (5xx) | The `<name>` application answered with a server error (`<status>`). |
| The application refused the call (401 after one token renewal, 403) | The `<name>` application refused this call (`<status>`). |
| Another error status (4xx) | The `<name>` application answered with an error (`<status>`). |
| The connection could not be made | The `<name>` application could not be reached. |
| The person's access ended | Your access for this request ended. (unchanged) |
| The platform refused the application's address and token (403, 404) | The `<name>` application is not available for this call. (unchanged) |
| Obtaining or renewing the application's access timed out | Main Sequence timed out while providing access to the `<name>` application. |
| The platform could not provide them for another reason | Main Sequence could not provide access to the `<name>` application. |
| Any other failure | The `<name>` application could not complete this call. |

The result's details carry the kind of failure (`failure`) and, when there is one, the status
(`status`) or the time limit that applied (`timeout_seconds`); the `runtime.mcp_application.failed`
log event carries the same and the error type. Neither carries a URL, a token, an exception message
or a response body.

Access resolution preserves transport timeout classification while discarding the exception chain
that holds credentials. These failures carry `failure: timeout`, `phase: access` and the platform
request's actual `timeout_seconds`. HTTP 408/504 responses carry `failure: timeout` and `status`,
without substituting the tool's configured limit for the unknown server limit. Other platform
failures remain `access_unavailable`; the existing retry boundaries still apply.

### 4. One retry, only before the call is sent

`<name>__call_tool` and `<name>__list_tools` open the application session once more when opening it
failed because the connection could not be made or was dropped, a request timed out, or the
application or gateway answered with a server error (5xx). Opening covers the connection, the MCP
handshake and the tool list; nothing has reached the tool yet, so a retry cannot repeat its work.
The second outcome is the one reported.

A failure after the tool call is sent is never retried, whatever its cause: the SDK cannot tell
whether the application started the work. A refusal, an ended access, a platform failure to provide
the address and token, and a cancelled turn are not retried.

### Amendment: readiness before MCP (#87)

A successful platform access response with `runtime_access.state: waking` and `access: null` is
temporary readiness, not a failure to obtain permission. Application access acquisition and token
renewal follow `retry_after_ms` until a grant is ready. Missing or invalid guidance defaults to
two seconds, with a 100 ms minimum to prevent busy polling. The readiness budget is
`TAU_MCP_APPLICATION_READY_TIMEOUT_SECONDS` (default 120 seconds), measured from access acquisition;
the remaining turn deadline and cancellation also bound the wait, including in-flight access
requests. No cache lock is held across readiness delays, and every poll keeps the original
delegation while obtaining the current lease proof. An ended turn never falls back to Agent access.

Only `waking` is polled. Permission denial, ended delegation and terminal unavailability remain
terminal. An expired readiness wait reports `readiness_timeout`, `phase: readiness`,
`runtime_access_state: waking` and its `timeout_seconds`; it is not a tool execution timeout.
Diagnostics log `runtime.application_access.waiting` with retry guidance, then
`runtime.application_access.ready` on success, without credentials. MCP starts only after access is
ready, and the tool's declared execution limit then applies. This amendment does not add retries
for a sent tool call or change the existing one-renewal-on-401 behavior.

### 5. Scope

Sections 2 to 4 apply to application tool calls. The Main Sequence MCP keeps
`MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS` and its current failure handling; section 1 applies
to it.

## Rejected alternatives

- **Time limits in the Agent's settings or workflow file.** The tool's author knows how long a tool
  runs. Every Agent that calls it would have to repeat and maintain the value, in environment
  variables.
- **One limit per application, sent by the platform in `mcp_applications`.** It needs a platform
  change for a value the application owns, and it treats fast and slow tools alike.
- **Reusing the tool list from an earlier `<name>__list_tools` in the turn.** The model can call a
  tool without listing first, and the session is opened per call anyway.
- **Retrying a 502 or 503 on the tool call itself.** A gateway answers 502 both when it could not
  reach the application and when the application dropped the connection mid-answer, so a retry
  could run a tool twice. A later decision may allow it for tools that declare `readOnlyHint` and
  `idempotentHint`, which the SDK now has from the tool list.

## Consequences

- Every application tool call first reads the application's catalog, as `<name>__list_tools` does:
  one more request for the tool list.
- A tool can run for as long as its author declares, up to the turn's limit; the person waits that
  long.
- The gateway in front of an application has its own limit. A declared time longer than that ends at
  the gateway and is reported as the gateway's answer, for example a gateway timeout (504). An
  application that declares long times needs a gateway that allows them.
- Applications written with other MCP libraries set `_meta` in their own way.
- With section 1, a failed Main Sequence MCP connection fails its calls instead of leaving them
  waiting. That connection is opened once per process and never reopened, so after it fails, its
  tools fail until the runtime restarts.

## Not in scope

Reopening the Main Sequence MCP connection after it fails; declared time limits and plain failure
words for the Main Sequence MCP's tools; progress notifications that extend a wait; retrying a call
after it was sent.

## Implementation and acceptance

1. Section 1: the connection client fails the call in progress together with the queued calls.
   Tests: a 403, 502 and 503 on a tool call end the call within its limit, on an application
   connection and on the Main Sequence MCP's client.
2. `TAU_MCP_TOOL_TIMEOUT_SECONDS`: default 60, greater than 0, in the
   [settings reference](../reference/settings.md).
3. Section 2. Tests: a tool that runs longer than `MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS`
   but within its declared time succeeds; a tool past its time returns a result that says it did
   not answer and after how many seconds; a tool without a declared time uses the default.
4. Section 3. Tests: one for each row of the table, and none of the results, details or log events
   carries a URL, token, exception message or response body.
5. Section 4. Tests: a 502 during the handshake or the tool list is retried once and the second
   answer is returned; a 502 on the tool call is not retried, and the gateway sees one call.
6. In the same change, document how to set a tool's limit in
   [Application tool time limits](../guides/application-tool-time-limits.md), and update the
   [runtime contract](../reference/runtime-contract.md), the
   [settings reference](../reference/settings.md), the
   [troubleshooting page](../reference/troubleshooting.md), the packaged project customization
   skill and the changelog.
