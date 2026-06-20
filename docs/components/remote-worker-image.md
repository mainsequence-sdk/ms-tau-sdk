# Remote Worker Image

This page documents [`Dockerfile.remote-worker`](../../Dockerfile.remote-worker), the image used
when Astro is layered onto an existing prepared project image.

For the local mounted-project harness, see:

- [`Dockerfile.remote-worker.local`](../../Dockerfile.remote-worker.local)
- the disabled `astro-project-executor` service example in [`../../docker-compose.yml`](../../docker-compose.yml)

That local harness assumes the host project path mounted into `/workspace/project` is already the
project root Astro should work against.

## Purpose

`Dockerfile.remote-worker` does not replace the normal Astro stream image.

It exists for a project-attached runtime context where:

- the backend already chose a project image
- that image already contains the canonical cloned project repository at `${SKEL_APP_DIR}`
- that image already installs the project's Python dependencies
- Astro should be layered on top so Pi can run inside that prepared project cwd

The resulting container has two roots:

- `/app`
  - Astro runtime bundle
  - contains Astro's `.pi`, `pi`, `interface`, `runtime`, `adapters`, `bin`, `tools`, and
    `node_modules`
- `${SKEL_APP_DIR}`
  - canonical cloned project tree from the existing project-image scaffold
  - defaults to `/usr/local/share/user-skel/app`

Astro bundle files live under `/app`, while Pi execution happens inside `${SKEL_APP_DIR}` because
`ASTRO_FIXED_PROJECT_CWD` points there.

## Build Structure

`Dockerfile.remote-worker` has three logical parts.

### 1. Published Astro Bundle

The first stage pulls a published Astro bundle image from Artifact Registry:

- `europe-west1-docker.pkg.dev/mainsequence-development/tsorm-images/astro/project-executor-bundle`
- the Dockerfile only varies the bundle tag via `ASTRO_EXECUTOR_BUNDLE_REF`

The image name is legacy. It is still the Astro runtime bundle consumed by project-attached
deployments.

### 2. Existing Project Image

The second stage starts directly from `BASE_IMAGE`.

That image is expected to have already done the project-specific work:

- clone or materialize the target repo
- install the project's dependencies
- expose the canonical cloned project directory at `${SKEL_APP_DIR}`
- preserve the existing user contract such as `NB_USER`, `NB_UID`, and `NB_GID`

`Dockerfile.remote-worker` does not repeat any of that work.

### 3. Astro Overlay

The final part adds Astro to the already-built project image:

- installs Node 20 if the base image does not already provide it
- copies the Astro runtime bundle into `/app`
- creates Astro runtime directories
- keeps `USER ${NB_USER}`
- keeps Docker `WORKDIR /app` so bundle-relative startup paths stay stable
- changes into `${SKEL_APP_DIR}` before launching Astro so the live process cwd is the project tree
- starts Astro with:

```bash
cd "${ASTRO_FIXED_PROJECT_CWD}" && tsx /app/bin/astro-stream.ts
```

## Runtime Environment Variables

The remote worker image sets Astro runtime env vars in the Dockerfile so the pod starts with a
prepared project cwd.

### Astro Runtime Paths

- `PATH=/app/node_modules/.bin:${PATH}`
- `HOME=/home/${NB_USER}`
- `ASTRO_CONTAINER_DATA_DIR=/home/${NB_USER}/.astro-container-data`
- `ASTRO_STREAM_HOST=0.0.0.0`
- `ASTRO_STREAM_PORT=8787`
- `ASTRO_STREAM_SESSION_DIR=/session-state/sessions`
- `ASTRO_SESSION_OVERRIDES_DIR=/session-state/session-overrides`
- `ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/${NB_USER}/.astro-container-data/.config/mainsequence`
- `PI_CODING_AGENT_DIR=/home/${NB_USER}/.astro-container-data/.pi/agent`
- `ASTRO_PI_PACKAGE_PATHS=/app/adapters/mainsequence/pi-overlay`

### Project-Attached Runtime Env

- `ASTRO_FIXED_PROJECT_CWD=${SKEL_APP_DIR}`
  - tells Astro where the prepared project code lives inside the image
- `ASTRO_PROJECT_IMAGE_REF=${BASE_IMAGE}`
  - records the image reference into project/session metadata

`ASTRO_FIXED_AGENT_TYPE` may still be injected at pod launch when the backend wants to pin a Main
Sequence backend identity such as `project-executor`, but it is not required to make the runtime
project-attached. `ASTRO_EXECUTION_MODE=remote_project_worker` is legacy topology metadata and is
not required by new project-attached deployments.

### Env Vars Usually Injected At Pod Launch

These are not baked into the Dockerfile because they depend on the specific backend/session:

- `BUILD_AGENTS_IN_BACKEND`
- `MAINSEQUENCE_BACKEND`
- Main Sequence runtime credential env vars when backend-backed auth is required
- `ASTRO_FIXED_AGENT_TYPE` only when the backend wants to pin identity

## Security And Writable Paths

The remote worker image is intended to run under the platform security context:

- `runAsUser: 10000`
- `runAsGroup: 10000`
- `fsGroup: 10000` when needed for mounted volumes

The remote worker expects these writable locations:

- `/session-state/sessions`
- `/session-state/session-overrides`
- `/home/${NB_USER}/.astro-container-data`
- `/home/${NB_USER}/.astro-container-data/.pi/agent/bin`
- `/home/${NB_USER}/.astro-container-data/.config/mainsequence`

The Dockerfile creates those directories and assigns ownership to `${NB_UID}:${NB_GID}`.

## Runtime Behavior

The image-backed contract is simple: the image should already contain the prepared project runtime,
so Astro executes work inside it instead of rebuilding it.

Astro still uses backend/session authority for model binding, checkpoints, provider credentials, and
capability materialization. Project attachment only changes the working cwd and available
project-local resources.

## Build Inputs

At build time, the downstream system needs:

- `BASE_IMAGE`
- optionally `ASTRO_EXECUTOR_BUNDLE_REF`

At pod launch time, the backend should provide:

- the runtime session identity used by the worker
- the user id used for deterministic agent registration
- any backend auth or runtime credential env required for session allocation and model use

## Published Bundle Image

This repo's Cloud Build publishes the Astro bundle image to:

```text
${_AR_REGION}-docker.pkg.dev/${PROJECT_ID}/${_AR_REPO}/astro/project-executor-bundle
```

It pushes:

- `:latest`
- `:astro-<astro-version>`

The image name is still legacy-compatible; it does not mean Astro has a separate internal
`project-executor` runtime architecture.

## Related Pages

- [`deployment-identities.md`](./deployment-identities.md)
- [`runtime-entrypoints-and-tools.md`](./runtime-entrypoints-and-tools.md)
- [`../interface/environment.md`](../interface/environment.md)
- [`../interface/overview.md`](../interface/overview.md)
