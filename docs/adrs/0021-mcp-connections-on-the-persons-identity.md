# ADR 0021: MCP Connections on the Person's Identity

Status: Accepted — SDK steps 1 to 3 implemented; they take effect as the platform meets its
requirements

Date: 2026-10-07

Amends:

- [ADR 0004: Minimal bundled tool boundary](./0004-minimal-bundled-tool-boundary.md) by replacing
  the single Main Sequence MCP connection with configured MCP connections;
- [ADR 0011: Independent base-tool and Main Sequence MCP exclusion](./0011-independent-base-tool-and-main-sequence-mcp-exclusion.md)
  by applying the exclusion to the platform's own connection only; and
- [ADR 0019: Verified request identity and session ownership](./0019-verified-request-identity-and-session-ownership.md)
  by letting a delegated Agent serve the person who started the work.

## Context

The SDK connects to one MCP server: the Main Sequence platform's own. The client is fixed to the
platform's `/mcp` URL and authenticates with the runtime credential, so every tool call acts as the
Agent's workload, with that workload's grants. The adapter that turns MCP tools into Tau tools is
specific to that server. Nothing can add another MCP server.

The platform also lets a FastAPI application serve its own MCP endpoint at `/mcp`. An application
receives the verified user on every request, as it does for its other routes. An Agent can reach
such an application today only from project code, over plain HTTP, through `requester_client()`.

Acting for the person is read-only and available only to project tools. A delegated Agent serves
nobody: when Agent A, working for a person, delegates to Agent B, B's turns have no requester.

## Decision

### 1. One general MCP connection primitive

The SDK connects to remote MCP servers over Streamable HTTP. Each connection has a name, used as its
tool prefix; a URL; a credential source; and a tool filter. One adapter serves every connection:
prefixing, schema and result projection, safety filters, parallel execution of read-only tools,
and resources.

The platform's own MCP becomes the first configured connection. It keeps its current extras: the
Environment argument is hidden, the A2A send rules apply, resources are listed in the prompt, and
the operations that return credentials or were withdrawn are never offered to the model.

Not supported: MCP servers that Main Sequence does not host, stdio servers, and the SSE transport.
A stdio server would need its program in the deployed image, so it is outside this decision.

### 2. Every MCP call runs as the person

Every MCP tool call acts for the person the turn serves, on the platform's own MCP and on
application MCP endpoints alike. The platform checks the person's access on every call.

- **Reads and writes.** The Agent can do on the platform what the person can do: read, and also
  change, create, run, share and delete, within that person's own rights. It never does more than
  the person could.
- **Admin approval.** An Agent uses MCP only when an Organization admin has enabled it to act for
  its requester. The managers of an Agent control its code; with this switch that code acts with
  the access of every person who uses the Agent, which no manager holds.
- **No person, no MCP.** A turn that serves nobody gets no MCP tool call.
- **How the platform moves a tool.** The platform marks each tool that runs for the person with
  the tool metadata key `mainsequence.ai/requires-requester/v1: true`. For such a tool the SDK
  sends the turn's private session proof, from which the platform finds the person, and in a
  hosted turn that serves nobody it refuses the call before sending it. A tool without the mark
  keeps running as the Agent's workload, so the platform can move tools one at a time and retire
  the workload path last.
- **The risk.** Model-driven code can be steered by prompt injection. With writes, the damage
  reaches as far as the person's own rights, for at most 24 hours after their request and only
  while the turn or Task serving it runs. This is why the admin approval stays, and why the
  documentation and the statement people are shown say so.

### 3. Delegation carries the person who started it

When an Agent working for a person delegates to another Agent, the delegated Agent acts for that
same person:

- the platform records the person on the delegated Task from its own record of the delegating
  turn, never from anything an Agent states;
- the limits stay: at most 24 hours from the person's original request, delegation never extends
  it, it ends when the person loses access, and a stop request on the delegating conversation
  withdraws it;
- a chain of delegations keeps the person and the original request time;
- the delegated Agent must itself be enabled to act for its requester;
- a delegation from a turn that serves nobody passes nobody on.

The SDK already gives a Task attempt that the platform dispatches the person the dispatch names, so
`current_requester()` covers delegated work once the platform names the person.

### 4. Application MCP endpoints

An Agent declares, in its workflow file, the applications whose MCP endpoints it uses. Declaring an
application registers its MCP. Neither the workflow file nor the runtime carries an application's
UID or URL: UIDs differ per Environment and do not exist before the first deploy. The platform
resolves each declared application in the Agent's Environment and hands the runtime its name and
release in the startup data, as `mcp_applications` entries with `name` and `resource_release_uid`.

Each application gets two tools:

- `<name>__list_tools` asks the application for its tools, with each tool's name, description and
  input schema; and
- `<name>__call_tool` calls one of them by name with its arguments.

Both run inside the turn for the turn's person. For each call the SDK asks the platform for the
application's address and a short-lived token for that person, opens an MCP session to the
application's `/mcp` endpoint, and closes it afterwards. No session, token or catalog is shared
between turns or people, and a turn that serves nobody is refused before anything is sent. The SDK
reads no catalog when the session loads, because no person is known then; the model lists an
application's tools when it needs them. Local mode has no declared applications.

### 5. Credentials

The SDK holds no long-lived credential for a connection. It uses short-lived tokens that the
platform issues for the turn's person. Tokens, proofs and assertions never enter model context,
tool arguments or results, session history, the UI stream or logs.

### 6. Local mode

Local mode already acts as the signed-in person. It uses the same connections with that person's
own credentials.

## Platform requirements

The SDK depends on the platform to:

1. accept MCP tool calls made for the turn's person, reads and writes: mark each such tool with
   `mainsequence.ai/requires-requester/v1`, find the person from the session proof sent with it,
   and check the person on every call;
2. record the person who started the work on a delegated Task, with the limits in section 3;
3. resolve the applications an Agent declares in its Environment and hand their names and releases
   to its runtime in the startup data (`mcp_applications`);
4. keep accepting MCP calls made as the Agent's workload until the released SDK versions that make
   them are retired. An SDK released before the mark sends the proof only for a tool that also
   carries `mainsequence.ai/requires-caller-session-proof/v1`; and
5. update the statement people are shown, so every client shows the same words.

## Consequences

- An Agent never has more access through MCP than the person it serves, and broad grants on Agents
  stop being needed for MCP.
- An Agent that an Organization admin has not enabled, and every turn that serves nobody, loses its
  MCP tools.
- Agent-to-agent delegation works for the person who started it.
- The security model page and the statement people are shown change: an enabled Agent can change
  things as the person, not only read.

## Not in scope

MCP servers that Main Sequence does not host, stdio servers, the SSE transport, per-person consent
for each Agent, and limiting `requester_client()` to the applications an Agent declares.

## Implementation and acceptance

1. Build the connection primitive and make the platform's own MCP its first connection, with no
   change in behavior.
2. Honor the requester mark: send the session proof with a marked tool, and refuse it in a hosted
   turn that serves nobody. This changes nothing until the platform marks tools under
   requirement 1.
3. Register the two tools for each application in `mcp_applications`. This changes nothing until
   the platform hands applications under requirement 3.
4. In the same change as each step, update the
   [agent security model](../reference/security-model.md), the public API reference and the
   packaged skills. The security model states the write risk in section 2, application
   connections, delegation and turns that serve nobody.
5. Tests show that tokens, proofs and assertions never reach model context, tool results, history
   or logs; that no MCP session or catalog is shared between people; and that a turn serving nobody
   is offered no MCP tools.
