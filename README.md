![Main Sequence logo](https://api.main-sequence.app/static/media/logos/MS_logo_long_black.png)

# Astro

Astro is a Pi deployment runtime that exposes Main Sequence assistant sessions over HTTP.

Astro now uses one internal Pi runtime shape. Backend identities such as `astro-orchestrator` and
`project-executor` remain valid Main Sequence session metadata, but local behavior is selected by
runtime context: primarily whether `ASTRO_FIXED_PROJECT_CWD` attaches the runtime to a prepared
project workspace. Both no-project and project-attached deployments use the shared package/system
prompt contract.

## Quick start

```bash
npm run pi
```

To run Astro over HTTP stream:

```bash
npm run pi:stream
```

To build the deployable container targets:

```bash
docker build --target astro-pi -t astro:pi .
docker build --target astro-pi-stream -t astro:pi-stream .
```

To bump the Astro project version before publishing a deployable image, use the release level that
matches the change. For a major runtime contract upgrade:

```bash
npm run version:major
```

Review and commit the version change, then publish the image:

```bash
npm run build:gcp
```

Set `MAINSEQUENCE_PIP_SPEC` in `.env` to control which `mainsequence` package spec the image installs in
its final Docker layer, for example `mainsequence==0.1.2`.

To use Docker Compose in live-mounted dev mode:

- `./.pi` -> `/app/.pi`
- `./pi` -> `/app/pi`
- `./interface` -> `/app/interface`
- `./runtime` -> `/app/runtime`
- `./adapters` -> `/app/adapters`
- `./bin` -> `/app/bin`
- `./tools` -> `/app/tools`
- `./docs` -> `/app/docs`
- `./README.md` -> `/app/README.md`
- `./package.json` -> `/app/package.json`
- `./package-lock.json` -> `/app/package-lock.json`
- `./tsconfig.json` -> `/app/tsconfig.json`
- tmpfs-backed `astro_session_emptydir` volume -> `/session-state` for local session files

```bash
docker compose run --rm astro-pi
docker compose up astro-pi-stream
```

`astro-pi` is behind the optional `pi-shell` profile, so a plain `docker compose up` starts the HTTP
stream service and the local checkpoint sidecar simulation. The standalone Pi container still works
when you target it explicitly with `docker compose run --rm astro-pi`.

The compose file now bind-mounts the editable Astro source files into `/app`, so normal code
changes do not require an image rebuild. It intentionally does not bind-mount the whole repo root,
which avoids clobbering the container's Linux `node_modules`. Restart the service to pick up code
edits:

```bash
docker compose restart astro-pi-stream
```

The compose file now keeps active session files in a shared tmpfs-backed `/session-state` volume and
leaves container runtime state rebuildable:

- `HOME=/home/jovyan`
- `ASTRO_CONTAINER_DATA_DIR=/home/jovyan/.astro-container-data`
- `ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/jovyan/.astro-container-data/.config/mainsequence`
- `PI_CODING_AGENT_DIR=/home/jovyan/.astro-container-data/.pi/agent`
- `ASTRO_SESSION_STATE_DIR=/session-state`
- `ASTRO_STREAM_SESSION_DIR=/session-state/sessions`
- `ASTRO_SESSION_OVERRIDES_DIR=/session-state/session-overrides`
- `ASTRO_PROVIDER_CREDENTIAL_DIR=/session-state/pi-agent-auth`

At startup, Astro prepares only container-local runtime state. Provider auth, provider signin state,
and stream session files have no host or repo-local source path in the container. Backend
checkpoints are the durable source for session continuity. The image runs as non-root `jovyan`;
rebuildable runtime state lives under `/home/jovyan/.astro-container-data`.

That keeps Linux virtualenvs isolated from macOS host paths and makes Docker behave closer to the
pod-local `emptyDir` session model.

To run the mounted-project local project-attached harness, set a host project path and launch the
disabled compose harness after enabling it locally:

```bash
export A2A_DEV_PROJECT=/Users/jose/mainsequence-dev/main-sequence-workbench/projects/hope-30-81
export ASTRO_EXECUTOR_PROJECT_ID=hope-30-81
docker compose up astro-project-executor
```

That local project-attached harness:

- starts the same Astro stream runtime with `ASTRO_FIXED_PROJECT_CWD=/workspace/project`
- mounts `A2A_DEV_PROJECT` into `/workspace/project`
- keeps backend `agentType` as session metadata, not local runtime architecture
- lives in the normal `docker-compose.yml` stack
- uses the same stream runtime as the no-project deployment

To run only the local project-attached runtime:

```bash
export A2A_DEV_PROJECT=/absolute/path/to/checked-out-project
export ASTRO_EXECUTOR_PROJECT_ID=<project-id>
docker compose up astro-project-executor
```

## Start reading here

- [`docs/README.md`](./docs/README.md)
  - project overview with the architecture diagram and ordered reading paths
- [`docs/getting-started/quickstart.md`](./docs/getting-started/quickstart.md)
  - shortest path to running Astro
- [`docs/getting-started/pi-primer.md`](./docs/getting-started/pi-primer.md)
  - Pi concepts used by Astro, explained for readers who are new to Pi

## Pi components in this repo

- [`docs/components/settings-and-system-prompt.md`](./docs/components/settings-and-system-prompt.md)
- [`docs/components/extensions.md`](./docs/components/extensions.md)
- [`docs/extensions/README.md`](./docs/extensions/README.md)
- [`docs/components/agents.md`](./docs/components/agents.md)
- [`docs/components/prompts.md`](./docs/components/prompts.md)
- [`docs/components/skills.md`](./docs/components/skills.md)
- [`docs/components/runtime-entrypoints-and-tools.md`](./docs/components/runtime-entrypoints-and-tools.md)
- [`docs/components/deployment-identities.md`](./docs/components/deployment-identities.md)
- [`docs/components/remote-worker-image.md`](./docs/components/remote-worker-image.md)
