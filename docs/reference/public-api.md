# Public Python API

The intentionally small public surface is exported from `ms_tau_sdk`:

```python
from ms_tau_sdk import TauSDKSettings, __version__, create_app
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
