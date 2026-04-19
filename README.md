![Main Sequence logo](https://api.main-sequence.app/static/media/logos/MS_logo_long_black.png)

# Astro

Astro is a Pi package that acts as a parent orchestrator for Main Sequence project assistants.

It translates user intent into a Main Sequence project workflow, helps select an existing project or create a new one, sets the project up locally, and delegates implementation or review to specialists. A separate prompt template exists for the optional tutorial-regression workflow.

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

Set `MAINSEQUENCE_PIP_SPEC` in `.env` to control which `mainsequence` package spec the image installs in
its final Docker layer, for example `mainsequence==0.1.2`.

To use Docker Compose in live-mounted dev mode:

- `./.astro` -> `/app/.astro-migration-source` (read-only, one-time migration source)
- `./.pi` -> `/app/.pi`
- `./pi` -> `/app/pi`
- `./interface` -> `/app/interface`
- `./scripts` -> `/app/scripts`
- `./docs` -> `/app/docs`
- `./README.md` -> `/app/README.md`
- `./package.json` -> `/app/package.json`
- `./package-lock.json` -> `/app/package-lock.json`
- `./tsconfig.json` -> `/app/tsconfig.json`
- `${HOME}/.pi/agent` -> `/home/appuser/.pi/host-agent` (read-only migration source for `auth.json` and `sessions/`)
- named volume `astro_container_data` -> `/home/appuser/.astro-container-data`

```bash
docker compose run --rm astro-pi
docker compose up astro-pi-stream
```

`astro-pi` is behind the optional `pi-shell` profile, so a plain `docker compose up` now starts only
the HTTP stream service. The standalone Pi container still works when you target it explicitly with
`docker compose run --rm astro-pi`.

The compose file now bind-mounts the editable Astro source files into `/app`, so normal code
changes do not require an image rebuild. It intentionally does not bind-mount the whole repo root,
which avoids clobbering the container's Linux `node_modules`. Restart the service to pick up code
edits:

```bash
docker compose restart astro-pi-stream
```

The compose file now treats the named volume as the canonical runtime state root:

- `ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/appuser/.astro-container-data/.config/mainsequence`
- `PI_CODING_AGENT_DIR=/home/appuser/.astro-container-data/.pi/agent`
- `ASTRO_STREAM_SESSION_DIR=/home/appuser/.astro-container-data/.astro/stream-sessions`

At startup, Astro performs a one-time migration from the read-only legacy mounts if the volume
does not yet have the PVC-layout marker, then keeps all active runtime state inside the volume.
The image now runs as non-root `appuser`, and the only valid durable runtime root is
`/home/appuser/.astro-container-data`.

That keeps Linux virtualenvs isolated from macOS host paths while still surviving container
recreation, and it makes Docker behave much closer to a single-PVC Kubernetes deployment.

To launch only the coding specialist instead of the full orchestrator for an already selected and checked-out project:

```bash
npm run specialist -- --agent mainsequence-project-coder --cwd /absolute/path/to/checked-out-project --project-id <project-id>
```

## Start reading here

- [`docs/README.md`](./docs/README.md)
  - canonical documentation hub with the architecture diagram and ordered reading paths
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
- [`docs/prompts/README.md`](./docs/prompts/README.md)
- [`docs/components/skills.md`](./docs/components/skills.md)
- [`docs/components/knowledge.md`](./docs/components/knowledge.md)
- [`docs/components/scripts-and-runtime.md`](./docs/components/scripts-and-runtime.md)

## Generated context

Astro no longer uses a generated `knowledge/` cache. All canonical docs live under `docs/`.
