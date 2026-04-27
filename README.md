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

To bump the Astro project patch version before publishing a deployable image:

```bash
npm run version:patch
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
- `./scripts` -> `/app/scripts`
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

- `ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/appuser/.astro-container-data/.config/mainsequence`
- `PI_CODING_AGENT_DIR=/home/appuser/.astro-container-data/.pi/agent`
- `ASTRO_STREAM_SESSION_DIR=/session-state/sessions`

At startup, Astro prepares only container-local runtime state. Provider auth, provider signin state,
and stream session files have no host or repo-local source path in the container. Backend
checkpoints are the durable source for session continuity. The image runs as non-root `appuser`;
rebuildable runtime state lives under `/home/appuser/.astro-container-data`.

That keeps Linux virtualenvs isolated from macOS host paths and makes Docker behave closer to the
pod-local `emptyDir` session model.

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
