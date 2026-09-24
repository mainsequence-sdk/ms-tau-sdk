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
  <a href="https://github.com/mainsequence-sdk/ms-tau-sdk/actions/workflows/publish-development-release.yml"><img src="https://github.com/mainsequence-sdk/ms-tau-sdk/actions/workflows/publish-development-release.yml/badge.svg?branch=development" alt="Development release publication"></a>
  <a href="https://github.com/mainsequence-sdk/ms-tau-sdk/issues"><img src="https://img.shields.io/github/issues/mainsequence-sdk/ms-tau-sdk.svg" alt="Open issues"></a>
</p>

`ms-tau-sdk` packages the Tau runtime integration, Main Sequence authentication and transports,
and durable agent-session machinery as a normal Python dependency. A project installs the SDK and
runs it from its own workspace—there is no separate Astro image, executor overlay, or second
deployment model.

| Contract | Value |
| --- | --- |
| PyPI distribution | `ms-tau-sdk` |
| Python packages | `ms_tau_sdk`, `ms_tau_board` |
| Commands | `ms-tau`, `tau-board` |
| Python entry point | `ms_tau_sdk.app:create_app` |
| Required Python | 3.13 or newer |
| Project customization | Standard workspace `.tau/` configuration |

## Quick start

Add the SDK to the project that will host the agent:

```bash
uv add ms-tau-sdk
```

Every push to `development` also publishes one `X.Y.Z.devN` release to the same PyPI project.
`pip` and `uv` skip development releases, so take one only on purpose:

```bash
uv add --prerelease=allow ms-tau-sdk   # or pin one exactly: uv add "ms-tau-sdk==X.Y.Z.devN"
```

See the [release process](docs/reference/releasing.md#registry-publication) for what each workflow
publishes.

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

Local conversations and public A2A Tasks are stored at
`~/.tau/mainsequence/<workspace-hash>/runtime.sqlite3`; an omitted chat `sessionUid` uses the
workspace default. Public A2A Message, Task, streaming, continuation, list/get/cancel, and
subscription flows run without creating a platform AgentSession. Incoming local A2A calls do not
need managed-gateway caller headers: supplied context IDs are mapped into the workspace-local
session namespace and local provenance is recorded. Task records and event streams survive process
restart.

Local mode also appends privacy-filtered, structured JSON Lines to
`~/.tau/mainsequence/<workspace-hash>/logs/tau.jsonl`, with bounded rotation. Both the database
and log move under `TAU_LOCAL_STATE_ROOT` when set; neither is written to the project `.tau` or
uploaded to a platform AgentSession. Console logging remains available.

For a small browser view of a local run, use the separately running
[Tau Board](packages/tau-board/README.md):

```bash
uv add 'ms-tau-sdk[tau-board]'
uv run tau-board --tau-url http://127.0.0.1:8010
```

Tau Board ships in the same SDK wheel. The `tau-board` extra declares its tested dependency
bounds; plain SDK installations never start the board.

Open `http://127.0.0.1:8788` to chat, send A2A Messages and Tasks, inspect local SQLite state,
and read logs. The board serves packaged Bulma CSS with no frontend build or CDN. It can select
another loopback Tau endpoint and local state directory in its Connect view. Its Settings tab shows
the board process's safe environment values and edits named non-secret variables in a local `.env`
file; Tau or the board must restart before saved startup values take effect.

Platform discovery of the unregistered process, internal backend dispatch/caller-delivery hooks,
push notifications, and `resume_caller` remain unavailable. When Main Sequence MCP is enabled,
outbound A2A supports Messages and Tasks with polling under authenticated-user semantics, and MCP
tools can read or mutate real platform resources. Local mode binds to `127.0.0.1` unless a host was
explicitly configured.

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
- local Tau execution without backend AgentSession pre-creation
- chat, SSE, A2A, health, and readiness transports
- optional Main Sequence MCP and always-present protocol-required task controls
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

Releases are immutable, and a merge to `main` is the release. Merging `development` into `main`
builds and verifies the wheel and source distribution of the version `pyproject.toml` declares,
publishes them to PyPI through OIDC trusted publishing, and tags the merge—for example, `v1.0.0`.
No PyPI API token or container registry is involved.

## Built on Tau

<p align="center">
  <a href="https://github.com/huggingface/tau">
    <img src="https://raw.githubusercontent.com/huggingface/tau/main/docs/assets/tau-header.svg" alt="Tau" width="760">
  </a>
</p>

Main Sequence TAU SDK integrates the open-source
[Tau coding agent](https://github.com/huggingface/tau) into the Main Sequence platform while
preserving Tau's workspace-native configuration and extension model.
