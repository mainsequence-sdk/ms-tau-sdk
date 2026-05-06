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
- that image already contains the canonical cloned project repository at `${SKEL_APP_DIR}`
- that image already installs the project's Python dependencies
- Astro should be layered on top so the pod can run `mainsequence-project-executor`

The resulting container has two distinct roots:

- `/app`
  - Astro runtime
  - copied from this repo
  - contains Astro's `.pi`, `pi`, `interface`, `scripts`, and `node_modules`
- `${SKEL_APP_DIR}`
  - canonical cloned project tree from the existing project-image scaffold
  - defaults to `/usr/local/share/user-skel/app`

That split is intentional. Astro still has a current runtime assumption that its own files live
under `/app`, while project execution should happen inside `${SKEL_APP_DIR}`.

## Build structure

`Dockerfile.remote-worker` has three logical parts.

### 1. Published Astro executor bundle

The first stage pulls a published `project-executor-bundle` image from Artifact Registry:

- `europe-west1-docker.pkg.dev/mainsequence-development/tsorm-images/project-executor-bundle`
- the Dockerfile only varies the bundle tag via `ASTRO_EXECUTOR_BUNDLE_REF`

That bundle is built from the Astro repo itself and carries the `/app` runtime tree needed by the
executor:

- `package.json`
- `package-lock.json`
- `tsconfig.json`
- `.pi/`
- `pi/`
- `interface/`
- `scripts/`
- `node_modules/`

This is what lets the downstream project-executor build work without checking out the Astro repo as
its Docker build context.

### 2. Existing project image

The second stage starts directly from `BASE_IMAGE`.

That image is expected to have already done the project-specific work:

- clone or materialize the target repo
- install the project's dependencies
- expose the canonical cloned project directory at `${SKEL_APP_DIR}`
- preserve the existing user contract such as `NB_USER`, `NB_UID`, and `NB_GID`

`Dockerfile.remote-worker` does not repeat any of that work.

### 3. Astro overlay

The final part adds Astro to the already-built project image:

- installs Node 20 if the base image does not already provide it
- copies the Astro runtime bundle into `/app`
- creates Astro runtime directories
- keeps `USER ${NB_USER}`
- keeps `WORKDIR ${SKEL_APP_DIR}`
- starts Astro with:

```bash
tsx /app/scripts/start_pi_stream.ts
```

## Build arguments

`Dockerfile.remote-worker` currently expects these build args:

- `BASE_IMAGE`
  - required
  - the already-built project image that contains the prepared project runtime
- `ASTRO_EXECUTOR_BUNDLE_REF`
  - optional
  - defaults to `latest`
  - selects the published tag from
    `europe-west1-docker.pkg.dev/mainsequence-development/tsorm-images/project-executor-bundle`

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
- `ASTRO_FIXED_PROJECT_CWD=${SKEL_APP_DIR}`
  - tells Astro where the project code lives inside the image
- `ASTRO_PROJECT_IMAGE_REF=${BASE_IMAGE}`
  - records the image reference into project-session metadata

### Deployed security contract

The remote worker image is intended to run under the platform security context:

- `runAsUser: 10000`
- `runAsGroup: 10000`
- `fsGroup: 10000` when needed for mounted volumes

To support that, the image:

- starts from `WORKDIR /usr/local/share/user-skel/app`
- keeps the real project path at `ASTRO_FIXED_PROJECT_CWD=/usr/local/share/user-skel/app`
- makes `/home/jovyan` itself owned by uid/gid `10000` so bootstrap can create runtime links there
- makes the canonical cloned project tree and Astro runtime state directories writable by uid/gid `10000`
- does not require `/app` ownership changes for runtime execution

### Env vars that are usually injected at pod launch

These are not baked into the Dockerfile because they depend on the specific backend session:

- `BUILD_AGENTS_IN_BACKEND`
  - required when the runtime should register and start backend agent sessions
- Main Sequence runtime credential env vars when backend-backed auth is still required by the
  runtime

For `mainsequence-project-executor`, Astro uses a fixed backend registration identity:

- backend agent name: `project-executor`
- backend `agent_unique_id`: `project-executor`
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

At build time, the downstream system only needs:

- `BASE_IMAGE`
- optionally `ASTRO_EXECUTOR_BUNDLE_REF`

## Cloud Build object upload

This repo's Cloud Build now uploads `Dockerfile.remote-worker` to:

```text
gs://${PROJECT_ID}/pod-dockerfiles/project_executor/Dockerfile
```

That object is overwritten on each build and is intended for downstream systems that build or
consume the remote worker image definition from Cloud Storage.

## Published bundle image

This repo's Cloud Build also publishes the executor bundle image to:

```text
${_AR_REGION}-docker.pkg.dev/${PROJECT_ID}/${_AR_REPO}/project-executor-bundle
```

It pushes:

- `:latest`
- `:astro-<astro-version>`

## Related pages

- [`scripts-and-runtime.md`](./scripts-and-runtime.md)
- [`../interface/environment.md`](../interface/environment.md)
- [`../interface/overview.md`](../interface/overview.md)
