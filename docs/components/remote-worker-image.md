# Remote worker image

This page documents [`Dockerfile.remote-worker`](../../Dockerfile.remote-worker), the image used for
image-backed `mainsequence-project-executor` pods.

For the local mounted-project executor harness, see:

- [`Dockerfile.remote-worker.local`](../../Dockerfile.remote-worker.local)
- the `astro-project-executor` service in [`../../docker-compose.yml`](../../docker-compose.yml)

That local harness assumes the host project path mounted into `/workspace/project` is already the
project root the executor should work against.

## Purpose

`Dockerfile.remote-worker` does **not** replace the normal Astro orchestrator image.

It exists for the separate runtime where:

- the backend already chose a project image
- that image already knows how to clone or materialize the project repository
- that image already installs the project's Python dependencies
- Astro should be layered on top so the pod can run `mainsequence-project-executor`

The resulting container has two distinct roots:

- `/app`
  - Astro runtime
  - copied from this repo
  - contains Astro's `.pi`, `pi`, `interface`, `scripts`, and `node_modules`
- `${APP_DIR}`
  - project runtime from the existing project-image scaffold
  - defaults to `/home/${NB_USER}/app`

That split is intentional. Astro still has a current runtime assumption that its own files live
under `/app`, while project execution should happen inside `${APP_DIR}`.

## Build structure

`Dockerfile.remote-worker` has three logical parts.

### 1. Astro runtime bundle

The first stage builds a Node bundle from this repo:

- installs npm dependencies
- copies:
  - `package.json`
  - `package-lock.json`
  - `tsconfig.json`
  - `.pi/`
  - `pi/`
  - `interface/`
  - `scripts/`
- runs `npm run check`

This stage produces a self-contained Astro runtime tree that can be copied into another image.

### 2. Existing project scaffold

The second stage preserves the existing project-image behavior and variable names:

- `BASE_IMAGE`
- `GIT_URL`
- `GIT_BRANCH`
- `GIT_HASH`
- `APP_DIR`
- `SKEL_DIR`
- `SKEL_APP_DIR`
- `NB_USER`
- `NB_UID`
- `NB_GID`

That stage:

- starts from `BASE_IMAGE`
- clones the target repo
- checks out `GIT_HASH`
- installs project Python dependencies
- keeps the runtime rooted at `${APP_DIR}`

This is the part that should stay aligned with the existing project image contract.

### 3. Astro overlay

The final part adds Astro to the already-built project image:

- installs Node 20 if the base image does not already provide it
- copies the Astro runtime bundle into `/app`
- creates Astro runtime directories
- keeps `USER ${NB_USER}`
- keeps `WORKDIR ${APP_DIR}`
- starts Astro with:

```bash
tsx /app/scripts/start_pi_stream.ts
```

## Build arguments

`Dockerfile.remote-worker` currently expects these build args:

- `BASE_IMAGE`
  - required
  - the existing project image or notebook-style base image used by your scaffold
- `GIT_URL`
  - required by the project scaffold
- `GIT_BRANCH`
  - required by the project scaffold
- `GIT_HASH`
  - required by the project scaffold

It also expects the same BuildKit secret used by the existing scaffold:

```bash
--secret id=ssh_private,src=/workspace/_ssh_private
```

## Runtime environment variables

The remote worker image sets Astro-specific runtime env vars in the Dockerfile so the pod starts in
executor mode without extra image edits.

### Astro runtime paths

- `PATH=/app/node_modules/.bin:${PATH}`
- `HOME=/home/${NB_USER}`
- `ASTRO_CONTAINER_DATA_DIR=/home/${NB_USER}/.astro-container-data`
- `ASTRO_STREAM_HOST=0.0.0.0`
- `ASTRO_STREAM_PORT=8787`
- `ASTRO_STREAM_SESSION_DIR=/session-state/sessions`
- `ASTRO_SESSION_OVERRIDES_DIR=/session-state/session-overrides`
- `ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/${NB_USER}/.astro-container-data/.config/mainsequence`
- `PI_CODING_AGENT_DIR=/home/${NB_USER}/.astro-container-data/.pi/agent`

### Executor-mode env vars

- `ASTRO_EXECUTION_MODE=remote_project_worker`
  - tells Astro this is an image-backed project worker
- `ASTRO_FIXED_AGENT_NAME=mainsequence-project-executor`
  - pins the runtime to the executor specialist
- `ASTRO_FIXED_PROJECT_CWD=${APP_DIR}`
  - tells Astro where the project code lives inside the image
- `ASTRO_PROJECT_IMAGE_REF=${BASE_IMAGE}`
  - records the image reference into project-session metadata

### Env vars that are usually injected at pod launch

These are not baked into the Dockerfile because they depend on the specific backend session:

- `BUILD_AGENTS_IN_BACKEND`
  - required when the runtime should register and start backend agent sessions
- Main Sequence runtime credential env vars when backend-backed auth is still required by the
  runtime

For `mainsequence-project-executor`, Astro derives the backend registration identity from the
project `.env` under `ASTRO_FIXED_PROJECT_CWD`:

- backend agent name: `project-executor`
- backend `agent_unique_id`: `project-executor-{MAINSEQUENCE_PROJECT_ID}`
- runtime agent name remains `mainsequence-project-executor`

## Filesystem layout and writable directories

The remote worker expects these writable locations:

- `/session-state/sessions`
- `/session-state/session-overrides`
- `/home/${NB_USER}/.astro-container-data`
- `/home/${NB_USER}/.astro-container-data/.pi/agent/bin`
- `/home/${NB_USER}/.astro-container-data/.config/mainsequence`

The Dockerfile creates those directories and assigns ownership to `${NB_UID}:${NB_GID}`.

## Current Astro behavior in remote worker mode

The current repo changes make the worker runtime behave differently from a normal
`mainsequence-project-coder` session:

- the runtime can be pinned to `mainsequence-project-executor`
- request-time Main Sequence CLI auth bootstrap is skipped when
  `ASTRO_EXECUTION_MODE=remote_project_worker`
- project sessions still require a deterministic `projectId`
- `mainsequence-project-executor` skips the checked-out-project bootstrap path that normally runs:
  - `mainsequence project sdk-status --path . --json`
  - `mainsequence project build_local_venv --path .`
  - `uv sync`
  - `.venv` activation

That is the core contract of the image-backed worker: the image should already contain the prepared
project runtime, so Astro should execute work inside it instead of rebuilding it.

## What the backend should pass

At pod launch time, the backend should provide at least:

- the runtime session identity used by the worker
- the user id used for deterministic agent registration
- any backend auth or runtime credential env required for session allocation and model use

The backend should treat the worker image digest as the durable runtime artifact and reuse that
image for resume or replay of the same executor session.

## Cloud Build object upload

This repo's Cloud Build now uploads `Dockerfile.remote-worker` to:

```text
gs://${PROJECT_ID}/pod-dockerfiles/astro-remote-worker/Dockerfile
```

That object is overwritten on each build and is intended for downstream systems that build or
consume the remote worker image definition from Cloud Storage.

## Related pages

- [`scripts-and-runtime.md`](./scripts-and-runtime.md)
- [`../interface/environment.md`](../interface/environment.md)
- [`../interface/overview.md`](../interface/overview.md)
