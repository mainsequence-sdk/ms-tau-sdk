# Quickstart

Main Sequence TAU SDK runs inside your project environment and uses the current directory as its
workspace.

## Install and lock

With `uv`:

```bash
uv add "ms-tau-sdk==1.2.0"
```

The project lockfile is the record of the exact SDK, Tau, provider, and transport versions that
will execute.

When a coding agent will develop or debug the TAU integration, explicitly copy the skills from the
installed SDK version:

```bash
uv run ms-tau skills sync --path .
```

The managed copies are written to `.agents/skills/ms_tau_sdk/`. Re-run the same command after an
SDK update. Installing the package and starting the runtime do not copy files automatically.

## Configure runtime authentication

Set the runtime credential supplied for the process:

```bash
export MAINSEQUENCE_RUNTIME_CREDENTIAL_ID="<runtime-credential-id>"
export MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET="<runtime-credential-secret>"
```

The SDK exchanges this pair for short-lived access credentials. Do not put either value in source
control or `.tau` files.

Set `MAINSEQUENCE_ENDPOINT` only when the project must use a non-default Main Sequence API URL.
`MAINSEQUENCE_BACKEND` remains accepted as a compatibility alias, but the established platform
endpoint variable takes precedence when both are present.

## Run

From the project root:

```bash
uv run ms-tau
```

The process listens on `0.0.0.0:8787` by default. `/health`, `/ready`, and `/version` report process
state without exposing secrets.

## Compose with an existing ASGI application

Create a project-owned shim such as `api/tau/main.py`:

```python
from ms_tau_sdk import create_app

app = create_app()
```

Then run the same application through your chosen ASGI server:

```bash
uv run uvicorn api.tau.main:app --host 0.0.0.0 --port 8787
```

Both entry paths use the same settings, routers, authentication client, Tau lifecycle, persistence,
streaming, and shutdown behavior.

## Run in local development mode

Use local mode when changing project code or `.tau` behavior and you do not want development
conversations to create or modify platform AgentSession state:

```bash
export MAINSEQUENCE_AUTH_MODE=jwt
export MAINSEQUENCE_ACCESS_TOKEN="<exported-user-access-token>"
export MAINSEQUENCE_REFRESH_TOKEN="<exported-user-refresh-token>"
export TAU_LOCAL_MODE=true
export TAU_LOCAL_PROVIDER=openai
export TAU_LOCAL_MODEL=gpt-5.4
uv run ms-tau
```

The Main Sequence login or project launcher is responsible for exporting the refreshable JWT
pair. The runtime package itself has no dependency on the `mainsequence` Python distribution and
does not read the CLI's private credential store.

Local mode creates its workspace-scoped SQLite state lazily on the first chat request. The request
may omit `sessionUid`; the response's `X-Agent-Session-Uid` header contains the effective local
identifier. Provider authorization, credential hydration, model inference, and Main Sequence MCP
remain remote. MCP tools operate on real platform resources.

Local mode defaults to `127.0.0.1:8787`. Explicitly binding another interface exposes a process
that acts with the authenticated user's live Main Sequence authority.

## Customize Tau

Add a project `.tau/SYSTEM.md` to replace the packaged behavioral default. Add project skills,
prompt templates, hooks, or extensions using Tau's native layout. See
[project configuration](../guides/project-configuration.md).
