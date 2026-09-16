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
construction, MCP, durable sessions, leases, and background tasks. Consumers should run the ASGI
lifespan rather than manually starting private services.

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
