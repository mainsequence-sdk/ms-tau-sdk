# Quickstart

Main Sequence TAU SDK runs inside your project environment and uses the current directory as its
workspace.

## Install and lock

With `uv`:

```bash
uv add ms-tau-sdk
```

The project lockfile is the record of the exact SDK, Tau, provider, and transport versions that
will execute.

## Configure runtime authentication

Set the runtime credential supplied for the process:

```bash
export MAINSEQUENCE_RUNTIME_CREDENTIAL_ID="<runtime-credential-id>"
export MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET="<runtime-credential-secret>"
```

The SDK exchanges this pair for short-lived access credentials. Do not put either value in source
control or `.tau` files.

Set `MAINSEQUENCE_BACKEND` only when the project must use a non-default Main Sequence API URL.

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

## Customize Tau

Add a project `.tau/SYSTEM.md` to replace the packaged behavioral default. Add project skills,
prompt templates, hooks, or extensions using Tau's native layout. See
[project configuration](../guides/project-configuration.md).
