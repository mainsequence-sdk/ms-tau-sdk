# Public Python API

The intentionally small public surface is exported from `ms_tau_sdk`:

```python
from ms_tau_sdk import TauSDKSettings, __version__, create_app
from ms_tau_sdk import current_requester, requester_client
```

## `create_app`

```python
create_app(settings: TauSDKSettings | None = None) -> FastAPI
```

Builds the complete FastAPI application without starting network dependencies. When no settings
object is supplied, settings are loaded once from environment variables and `.env`.

The application lifespan owns startup and shutdown of authentication, the backend client, provider
construction, MCP, durable sessions, leases, background tasks, and the local A2A Task reconciler.
Consumers should run the ASGI lifespan rather than manually starting private services.

`create_app` also installs request identity. It declares it to the platform launcher in
`app.state.mainsequence_request_identity` as `{"installed": True, "mode": ..., "public_ingress": ()}`,
with `mode` `assertion` when the platform hosts the runtime and `local` otherwise. In hosted mode
it admits a request only with the platform's signed assertion, and handlers read the verified
caller from `request.state.user` (`uid`, `team_uids`, `is_organization_admin`) and
`request.state.user_uid`. The launcher reads the declaration from the application object it serves:
serve the application `create_app` returns rather than mounting it inside another one, and do not
install a second request-identity integration on it. See the
[runtime contract](./runtime-contract.md#request-identity).

## `TauSDKSettings`

A Pydantic settings model for process, workspace, transport, persistence, and logging controls. A
consumer may construct it explicitly for embedding or tests:

```python
from pathlib import Path

from ms_tau_sdk import TauSDKSettings, create_app

settings = TauSDKSettings(workspace=Path.cwd())
app = create_app(settings)
```

The backend client, routers, provider adapters, session storage, and runtime manager are internal
implementation boundaries. Importing them does not create a compatibility promise.

## `current_requester` and `requester_client`

An Agent that an Organization admin enabled for it can read platform data, and call other platform
applications, with the access of the person whose request a turn is serving: the requester. The
platform keeps that authority. The runtime only proves which of its own sessions it is working on,
and the platform finds the person in its own records. No call names a person.

### `current_requester`

`current_requester()` returns the verified person the current turn serves, an immutable object
with `uid` (a User UID) and `team_uids` (a tuple of Team UIDs, empty when the turn does not know
them), or `None`:

| Turn | `current_requester()` |
| --- | --- |
| Chat (`POST /api/chat`) or A2A Message turn (`message:send` and JSON-RPC `SendMessage` answered with a Message) of a hosted runtime | The verified caller of the request, when the platform recorded that person as the turn's requester. The platform records a person who owns the session; it records nobody for an Agent caller. |
| A2A Task attempt started by the platform's dispatch | The person the dispatch names as the Task's requester. `team_uids` is empty. |
| A2A Task attempt started by the request that created or continued the Task (`message:send` answered with a Task, `message:stream`) | `None` |
| Agent callers, the platform's own calls (caller delivery), local mode, a runtime that is not hosted, and code outside a turn, such as Tau Board's tool workbench | `None` |

When a hosted chat or A2A Message turn starts, the runtime presents its request's verified caller
assertion with the transition that marks the turn active, so that the platform can record who
asked; a Task attempt never presents one. The assertion is not available to tools, and it is never
logged, persisted, or placed in model context, tool results, the UI stream or history.

`current_requester()` is valid only while the turn runs. When the turn ends it returns `None`,
also in a task that the tool started and left running.

### `requester_client`

`requester_client()` returns a client that makes requester-bound calls for the current turn. When
`current_requester()` is `None` it raises a `PermissionError` whose `code` is
`requester_binding_invalid`.

```python
import json

from tau_agent.messages import TextContent
from tau_agent.tools import AgentToolResult

from ms_tau_sdk import current_requester, requester_client

ANALYST_DATA_RELEASE_UID = "..."  # the release of the application to ask


async def revenue_by_region(tool_call_id, arguments, signal=None, on_update=None):
    requester = current_requester()
    if requester is None:
        return AgentToolResult(content=[TextContent(text="Ask me from your own chat.")])
    client = requester_client()
    try:
        answer = await client.call_release(
            ANALYST_DATA_RELEASE_UID,
            "POST",
            "/query",
            json={"question": "revenue by region"},
        )
    except PermissionError:
        return AgentToolResult(content=[TextContent(text="Your access for this request ended.")])
    return AgentToolResult(content=[TextContent(text=json.dumps(answer.json()["rows"]))])
```

- `await client.request(method, path, *, params=None, json=None, content=None, data=None,
  files=None, headers=None)` calls a platform API path, relative to the platform base URL
  (`MAINSEQUENCE_ENDPOINT`). It sends the runtime's own credential and the turn's session and lease
  proof: `X-MainSequence-Acting-For-Session`, `X-MainSequence-Lease-Holder` and
  `X-MainSequence-Lease-Token`. These headers never go to any other origin.
- `await client.call_release(release_uid, method, path, *, ...)` calls another platform
  application, which answers as the requester. The client obtains access with
  `POST /api/v1/resource-releases/<release_uid>/resolve-runtime-access/`, sent with the same three
  headers, then calls the application's RPC URL plus `path` with only
  `Authorization: Bearer <token>`. It keeps the token for the turn while it is valid, replaces it
  shortly before it expires, and replaces it once when the application answers 401.
- Both return an `httpx.Response` with the answer's status, headers and body. Its `request`
  carries no header, so neither the credential, the lease proof nor the application token travels
  with it. Redirects are not followed.
- `path` is a path only. A URL, a path that starts with `//`, `release_uid` that is not a canonical
  UUID, or a header that sets `Authorization` or any `X-MainSequence-*` header raises `ValueError`
  before anything is sent.
- A 403 whose JSON `code` is `requester_binding_invalid` or starts with `runtime_lease_` raises a
  `PermissionError` with that `code`: the requester's binding ended because the turn is over, the
  requester's access was removed, more than 24 hours passed since the request, or the Agent is not
  enabled to act for its requester. Every other answer is returned as it is.
- Requester-bound calls are read-only, at the requester's member level. The platform answers only
  the reads it opted in for them, and refuses writes, sharing, and Secret values.
- The client is bound to the turn that created it. After the turn ends every call raises the same
  `PermissionError` without being sent.

Return only business results to the model: never the response object, its headers, a token, or
a proof. People who use such an Agent are told:

> **This Agent works with your identity, securely.** It reads only what you can already read, only
> to answer your own requests, and for at most 24 hours after you ask. It cannot act as anyone else,
> cannot change, share or delete anything, never sees your secret values, and stops the moment your
> access ends. Your Organization's administrator approved it to work this way.

The limit is plain: while it works on your request, the Agent's code can read what you can read,
which is why only administrators decide which Agents may work this way. See the 2026-10-06
amendment of [ADR 0019](../adrs/0019-verified-request-identity-and-session-ownership.md) and the
[runtime contract](./runtime-contract.md#the-turns-requester).

## `__version__`

The installed distribution version. Runtime health/version reporting reads the same package
metadata, so a project can correlate Python composition with the serving process.

## Command

`ms-tau` resolves `TauSDKSettings`, builds the same application with `create_app`, and runs Uvicorn.

## A2A Task history

The HTTP application projects durable Task communication as the optional A2A `Task.history`
array. This is Message history only: requester Messages and deliberately persisted responder/status
Messages. Artifacts, Tau entries, Task events, logs, prompts, and tool traffic are not history.

`configuration.historyLength` applies to REST/JSON-RPC Message send and stream operations;
`historyLength` applies to Task get and list. Omission requests the SDK's bounded default tail of
100 Messages, zero performs no history-tail read and omits `history`, and a positive integer returns
at most that many latest Messages ordered oldest-to-newest. Values above 100 are capped; booleans,
negative values, and non-integers are rejected.

The public A2A v1 projection uses `ROLE_USER` and `ROLE_AGENT`. The SDK translates those roles to
its persistence binding at ingress and back at egress without changing Message identity, Parts,
metadata, extension URI order, or Task references. Status settlement accepts only a complete
responder Message or no Message; the pre-cutover `{code, message}` status object is not supported.

## Local chat sessions

Local mode records every `POST /api/chat` session and serves it back after a UI reload or a Tau
restart:

```text
GET /api/local/v1/chat-sessions?limit=50&cursor=<opaque>
GET /api/local/v1/chat-sessions/{sessionUid}/history
GET /api/local/v1/agent
```

The list returns the authenticated local process user's sessions, newest activity first, each with
its canonical `sessionUid` (the value of `X-Agent-Session-Uid`), title, message count, latest-text
preview, creation and activity times, and whether a turn is `working`. The history returns the
envelope of the platform's `GET /api/v1/agent-sessions/{uid}/history/`: `version`, `session`,
`messages`, and `inProgressMessage`. Messages carry user text, assistant text, reasoning, and tool
calls with their arguments, results, and `isError`. A turn still running is returned in
`inProgressMessage` with session status `running`. Continue a session by sending its `sessionUid`
to `POST /api/chat`.

`GET /api/local/v1/agent` returns `{name, displayName, description}` from the workspace's
`.agents/agent_card.json`, or nulls without a readable card. All three routes return 409 in managed
mode, where the platform owns sessions and history.

A local chat turn keeps running when its client disconnects; stop it with
`POST /api/chat/session/cancel`. While a session has a running turn, `POST /api/chat` for it returns
409 `session_busy`.

## Local direct A2A conversation history

Local mode exposes an SDK-owned extension for direct `message:send` conversations:

```text
GET /api/local/v1/conversations?limit=50&cursor=<opaque>
GET /api/local/v1/conversations/{contextId}/messages?limit=100&beforeSequence=<n>
```

The first route lists the authenticated local process user's workspace conversations with stable
canonical context IDs, deterministic titles, message counts, latest-text previews, activity times,
and bounded cursor pagination. The second returns persisted public requester/responder Messages
oldest-to-newest within the selected tail. Reuse the listed `contextId` in
`POST /api/a2a/v1/message:send` to continue after a UI or Tau restart.

This extension is not an A2A v1 method and is unavailable in managed mode. Its Messages are not
derived from Tau entries and never include system instructions, reasoning, tool activity, events,
or logs. A2A Task conversations remain under `Task.history`.
