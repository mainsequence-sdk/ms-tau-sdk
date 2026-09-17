<p align="center">
  <a href="https://www.main-sequence.io">
    <img src="https://www.main-sequence.io/images/logos/MS_logo_long_black.png" alt="Main Sequence" width="460">
  </a>
</p>

<h1 align="center">Main Sequence TAU SDK</h1>

<p align="center">
  <strong>Run Tau as a workspace-native Main Sequence coding agent.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/ms-tau-sdk/"><img src="https://img.shields.io/pypi/v/ms-tau-sdk.svg?logo=pypi&amp;logoColor=white" alt="PyPI version"></a>
  <a href="https://pypi.org/project/ms-tau-sdk/"><img src="https://img.shields.io/pypi/pyversions/ms-tau-sdk.svg" alt="Supported Python versions"></a>
  <a href="https://github.com/mainsequence-sdk/ms-tau-sdk/actions/workflows/quality.yml"><img src="https://github.com/mainsequence-sdk/ms-tau-sdk/actions/workflows/quality.yml/badge.svg?branch=development" alt="Quality checks"></a>
  <a href="https://github.com/mainsequence-sdk/ms-tau-sdk/actions/workflows/publish-to-pipy.yml"><img src="https://github.com/mainsequence-sdk/ms-tau-sdk/actions/workflows/publish-to-pipy.yml/badge.svg" alt="PyPI publication"></a>
  <a href="https://github.com/mainsequence-sdk/ms-tau-sdk/issues"><img src="https://img.shields.io/github/issues/mainsequence-sdk/ms-tau-sdk.svg" alt="Open issues"></a>
</p>

`ms-tau-sdk` packages the Tau runtime integration, Main Sequence authentication and transports,
and durable agent-session machinery as a normal Python dependency. A project installs the SDK and
runs it from its own workspace—there is no separate Astro image, executor overlay, or second
deployment model.

| Contract | Value |
| --- | --- |
| PyPI distribution | `ms-tau-sdk` |
| Python package | `ms_tau_sdk` |
| Command | `ms-tau` |
| Python entry point | `ms_tau_sdk.app:create_app` |
| Required Python | 3.13 or newer |
| Project customization | Standard workspace `.tau/` configuration |

## Quick start

Add the SDK to the project that will host the agent:

```bash
uv add ms-tau-sdk
```

Provide the runtime credential that Main Sequence assigned to the deployment:

```bash
export MAINSEQUENCE_ENDPOINT="https://api.main-sequence.app"
export MAINSEQUENCE_RUNTIME_CREDENTIAL_ID="<runtime-credential-id>"
export MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET="<runtime-credential-secret>"
```

Start the service from the project workspace:

```bash
uv run ms-tau
```

Install the version-matched development skills when a coding agent will work on the TAU
integration:

```bash
uv run ms-tau skills sync --path .
```

This explicit command owns only `.agents/skills/ms_tau_sdk/`; package installation and runtime
startup never modify the repository automatically.

The credential pair is exchanged for short-lived Main Sequence access tokens. Do not commit it or
place it in `.tau` configuration. For an embedded ASGI deployment, construct the same application
in Python:

```python
from ms_tau_sdk import create_app

app = create_app()
```

## Local development without platform sessions

Local mode runs the same workspace Tau runtime without registering an Agent or AgentSession. The
project selects a provider and model explicitly, while Main Sequence still authorizes and hydrates
the provider credential and supplies the live MCP catalog:

```bash
export MAINSEQUENCE_AUTH_MODE=jwt
export MAINSEQUENCE_ACCESS_TOKEN="<exported-user-access-token>"
export MAINSEQUENCE_REFRESH_TOKEN="<exported-user-refresh-token>"
export TAU_LOCAL_MODE=true
export TAU_LOCAL_PROVIDER=openai
export TAU_LOCAL_MODEL=gpt-5.4

uv run ms-tau
```

The normal Main Sequence login or project launcher may provision those JWT variables, but
`ms-tau-sdk` does not install, import, or invoke the `mainsequence` Python package. It consumes the
environment handoff and public refresh API directly. Provider secrets are never environment
settings and are never persisted locally.

Local conversations are stored at
`~/.tau/mainsequence/<workspace-hash>/runtime.sqlite3`; an omitted chat `sessionUid` uses the
workspace default. Local mode binds to `127.0.0.1` unless a host was explicitly configured. Main
Sequence MCP remains live, so its tools can still read or mutate real platform resources.

## Workspace-owned Tau behavior

The consuming repository owns the effective Tau configuration. It can override the packaged Tau
defaults and install project-specific tools through the normal `.tau/` structure:

```text
your-project/
├── .tau/
│   ├── SYSTEM.md
│   ├── settings.json
│   └── extensions/
├── pyproject.toml
└── uv.lock
```

Extensions run as project code in the same process and trust boundary as the rest of the
repository. Optional capabilities such as general web access belong in a project extension; they
are not bundled into the SDK. Main Sequence transport and protocol behavior remains SDK-owned.

## Included capabilities

- FastAPI application construction and lifecycle management
- runtime-credential exchange, local user-JWT refresh, and authenticated Main Sequence access
- provider validation and credential hydration
- durable Tau sessions, leases, restore, persistence, cancellation, eviction, and shutdown
- sessionless Tau execution
- chat, responses, SSE, A2A, health, and readiness transports
- Main Sequence MCP and protocol-required task controls
- packaged defaults that participate in Tau's normal workspace configuration
- explicit, version-matched development skills for repository integration, local debugging,
  project customization, and TAU's A2A host adapter

## Deployment boundary

This repository publishes Python distributions only. It contains no Dockerfile, Compose stack,
Kubernetes manifest, runtime image, executor bundle, or container-publication pipeline. The
consuming project owns its dependency lock, deployable artifact, system dependencies, project
code, prompts, skills, hooks, and extensions.

The project identity and migration are defined by
[ADR 56](./docs/adrs/adr-56-main-sequence-tau-sdk-workspace-bound-library-deployment.md). See the
[quickstart](./docs/getting-started/quickstart.md), [documentation index](./docs/README.md), and
[release guide](./docs/reference/releasing.md) for the complete contracts.

## Development

The repository uses Python 3.13 and `uv`:

```bash
uv sync --frozen
uv run pytest
uv run ruff check .
uv run mypy
```

Releases are immutable and tag-driven. Pushing a tag that exactly matches the package version—for
example, `v1.0.0`—builds and verifies the wheel and source distribution, then publishes them to
PyPI through OIDC trusted publishing. No PyPI API token or container registry is involved.

## Built on Tau

<p align="center">
  <a href="https://github.com/huggingface/tau">
    <img src="https://raw.githubusercontent.com/huggingface/tau/main/docs/assets/tau-header.svg" alt="Tau" width="760">
  </a>
</p>

Main Sequence TAU SDK integrates the open-source
[Tau coding agent](https://github.com/huggingface/tau) into the Main Sequence platform while
preserving Tau's workspace-native configuration and extension model.
