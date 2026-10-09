# Application Tool Time Limits

An Agent calls the tools of the applications it declares through `<name>__call_tool`. Each call
waits for the time limit its tool declares. A tool that declares none gets the Agent's default,
60 seconds unless the Agent changes it. The decision is
[ADR 0022](../adrs/0022-mcp-application-tool-call-limits-and-failures.md).

## Set a tool's time limit

The application that serves the tool sets its limit, next to the tool's code. Put a positive
number of seconds in the tool's `_meta` under `mainsequence.ai/timeout-seconds/v1`.

With the MCP Python SDK, pass it to the tool decorator:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("orders")


@mcp.tool(meta={"mainsequence.ai/timeout-seconds/v1": 300})
async def rebuild_report(month: str) -> str:
    """Rebuild the monthly report; this can take a few minutes."""
    ...


@mcp.tool()
async def list_orders(limit: int = 20) -> list[dict]:
    """List open orders. Declares nothing, so it gets the Agent's default."""
    ...
```

With another MCP library, the tool's entry in the `tools/list` answer must carry the key:

```json
{
  "name": "rebuild_report",
  "inputSchema": {"type": "object", "properties": {"month": {"type": "string"}}},
  "_meta": {"mainsequence.ai/timeout-seconds/v1": 300}
}
```

The value is a number of seconds greater than zero; fractions are allowed. A zero, negative,
non-numeric or infinite value is ignored: the call gets the default, and the Agent logs
`runtime.mcp_application.invalid_timeout` with the application and tool names. Declare a limit for
the tools that need more, or less, than the default; leave the others alone.

The Agent reads the application's tool list each time it opens a session for a call, so a new limit
applies to the next call after the application is deployed. Nothing changes in the Agent.

## Change an Agent's default

`TAU_MCP_TOOL_TIMEOUT_SECONDS` sets the limit for every tool that declares none; it defaults to
`60` and must be greater than zero. In a managed deployment, set it in the Agent's workflow file,
under `harness_agent.spec.env_vars`:

```yaml
resources:
  - key: agent
    kind: harness_agent
    spec:
      # The Agent's other settings.
      env_vars:
        - name: TAU_MCP_TOOL_TIMEOUT_SECONDS
          value: "120"
```

For local `ms-tau`, set it in the process environment or the project's `.env`. A Python host that
builds the application can pass the matching `TauSDKSettings` field instead:

```python
from ms_tau_sdk import TauSDKSettings, create_app

app = create_app(TauSDKSettings(mcp_tool_timeout_seconds=120))
```

Like the other process settings, it applies to every session in the process.

## What else bounds a call

- **The turn.** A call never outlives its turn: when `MAINSEQUENCE_TAU_TURN_TIMEOUT_SECONDS`
  (900 seconds by default) runs out, the call ends with the turn. A limit longer than the turn has
  no effect.
- **The gateway.** The gateway in front of the application has its own limit. A call that runs
  past it ends at the gateway, and the model reads the gateway's answer, for example a server
  error (504). An application that declares long limits needs a gateway that allows them.
- **Opening the session.** Before the call, the Agent connects to the application, makes the MCP
  handshake and reads the tool list. The connection waits
  `MAINSEQUENCE_TAU_BACKEND_CONNECT_TIMEOUT_SECONDS` (10 seconds by default), and the handshake and
  the tool list each wait `MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS` (60 seconds by default),
  not the tool's limit.

`MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS` no longer limits the call itself.

## What the model reads when a call fails

The tool result says what happened in plain words, without URLs, tokens, error text or response
bodies:

| What happened | What the model reads |
| --- | --- |
| The tool did not answer within its limit | The `<name>` tool `<tool>` did not answer within `<N>` seconds. |
| Opening the session did not finish within its limit | The `<name>` application did not answer within `<N>` seconds. |
| A server or gateway error status (5xx) | The `<name>` application answered with a server error (`<status>`). |
| The application refused the call (401 after one token renewal, 403) | The `<name>` application refused this call (`<status>`). |
| Another error status (4xx) | The `<name>` application answered with an error (`<status>`). |
| The connection could not be made | The `<name>` application could not be reached. |
| The person's access ended | Your access for this request ended. |
| The platform refused the application's address and token (403, 404) | The `<name>` application is not available for this call. |
| The platform could not provide them for another reason | Main Sequence could not provide access to the `<name>` application. |
| Any other failure | The `<name>` application could not complete this call. |

The result's details carry `failure` (for example `timeout` or `server_error`) and, when there is
one, `status` or `timeout_seconds`. The `runtime.mcp_application.failed` log event carries the same
fields and the error type.

## Retries

When opening the session fails because the connection could not be made or was dropped, a request
timed out, or the application or gateway answered with a server error, the Agent opens it once more
and reports the second outcome. Nothing has reached the tool at that point.

Once the call is sent, it is never sent again, whatever happens: the Agent cannot tell whether the
tool started its work. A refusal, an ended access, a platform failure to provide the application's
address and token, and a cancelled turn are not retried either.
