# GCP Deployment Plan

## Recommendation

This repo is not a purely stateless web container. The runtime expects durable local state under:

- `/home/appuser/.astro-container-data`

That state includes Main Sequence auth, Pi runtime state, stream sessions, SSH material, and other
runtime artifacts. Because of that, the safest production target on GCP is:

- `Cloud Build` for build/push
- `Artifact Registry` for the image
- the existing `GKE` workload for runtime

It also does not make Cloud Build deploy to GKE.
Cloud Build should only publish the container image to Artifact Registry.
The already-deployed GKE service remains responsible for runtime configuration and storage.

## Planned GCP Files

```text
deployment/
  gcp/
    README.md
    cloudbuild.yaml
```

## What `cloudbuild.yaml` Should Do

The current `deployment/gcp/cloudbuild.yaml` has four responsibilities:

1. Build the `astro-pi-stream` Docker target from the repo `Dockerfile`
2. Detect the Astro package version plus which `mainsequence`, Python, and Node versions were actually installed in the image
3. Stamp OCI labels on the final image with the exact full detected versions
4. Push three tags to Artifact Registry:
   one tag for the Astro package version, one descriptive runtime tag, and one `latest` tag

Suggested high-level flow:

```yaml
steps:
  - name: Build runtime image
    uses: docker build
    notes:
      - target should be astro-pi-stream
      - rely on the Dockerfile default so `mainsequence` resolves to latest

  - name: Detect Astro and installed mainsequence versions
    uses: docker run + python importlib.metadata

  - name: Detect installed python and node versions
    uses: docker run + python/node version commands

  - name: Stamp OCI labels for exact versions
    uses: docker commit --change LABEL=...

  - name: Push version tag
    uses: docker push

  - name: Push descriptive runtime tag
    uses: docker push

  - name: Push latest tag
    uses: docker push
```

## Recommended Substitutions

These are the substitutions that belong in `cloudbuild.yaml` for a registry-only pipeline:

- `_AR_REGION`
- `_AR_REPO`
- `_IMAGE_NAME`
- `_DOCKER_TARGET`
- `_IMAGE_PREFIX`
- `_LATEST_IMAGE`

Reasoning:

- image/repository settings change by environment and belong in substitutions
- the registry location is clearer when region is split out exactly like `${_AR_REGION}-docker.pkg.dev`
- `_IMAGE_PREFIX` keeps the dynamic version tag simple to compute inside the build step
- `_LATEST_IMAGE` stays explicit and readable in the push step

Current intended Artifact Registry path layout:

- repository: `tsorm-images`
- image path inside the repository: `astro/astro-pi-stream`
- resulting prefix:
  `europe-west1-docker.pkg.dev/${PROJECT_ID}/tsorm-images/astro/astro-pi-stream`

## Runtime Env For The Existing GKE Workload

Cloud Build should not manage these values.
They belong to the already-deployed GKE workload outside this repo's image publication pipeline.

### Values That Should Stay Fixed At Runtime

These are container contract values from the repo and should usually stay fixed in the running
service instead of varying per environment:

- `HOME=/home/appuser`
- `ASTRO_STREAM_HOST=0.0.0.0`
- `ASTRO_CONTAINER_DATA_DIR=/home/appuser/.astro-container-data`
- `ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/appuser/.astro-container-data/.config/mainsequence`
- `PI_CODING_AGENT_DIR=/home/appuser/.astro-container-data/.pi/agent`
- `ASTRO_STREAM_SESSION_DIR=/home/appuser/.astro-container-data/.astro/stream-sessions`

These come directly from the Dockerfile, compose file, and persistent-state docs.

### Values That Should Come From Secrets, Not Cloud Build Substitutions

Do not put these in `cloudbuild.yaml` substitutions:

- `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID`
- `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_OAUTH_TOKEN`
- `GEMINI_API_KEY`
- `GROQ_API_KEY`
- `CEREBRAS_API_KEY`
- `XAI_API_KEY`
- `OPENROUTER_API_KEY`
- `AI_GATEWAY_API_KEY`
- `ZAI_API_KEY`
- `MISTRAL_API_KEY`
- `MINIMAX_API_KEY`
- `MINIMAX_CN_API_KEY`
- `OPENCODE_API_KEY`
- `KIMI_API_KEY`
- `AZURE_OPENAI_API_KEY`

These should be injected into the existing runtime through your current secret-management path.

### Non-Secret Runtime Config

These are the main non-secret runtime values the existing GKE service should already provide:

- `MAINSEQUENCE_AUTH_MODE=runtime_credential`
- `MAINSEQUENCE_BACKEND` if the pod should use a non-default backend
- `BUILD_AGENTS_IN_BACKEND`
- `ASTRO_STREAM_PORT`
- `ASTRO_STREAM_TRUSTED_ORIGINS`
- `OLLAMA_HOST`

### Concrete Split For The Current Env

For the values currently shown in local `.env`, the split is:

Set these on the existing GKE workload when the pod is created or updated:

```env
MAINSEQUENCE_AUTH_MODE=runtime_credential
MAINSEQUENCE_BACKEND=http://192.168.1.111:8000
BUILD_AGENTS_IN_BACKEND=true
OLLAMA_HOST=http://192.168.1.10:11434
ASTRO_STREAM_TRUSTED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

Do not pass this one to the pod as runtime env:

```env
MAINSEQUENCE_PIP_SPEC=mainsequence==3.17.53
```

`MAINSEQUENCE_PIP_SPEC` is a build-time concern only.
With the current `cloudbuild.yaml`, we intentionally do not set it there either.
The Dockerfile default `MAINSEQUENCE_PIP_SPEC=mainsequence` is used so the image installs the
latest available `mainsequence`, then Cloud Build detects the Astro project version and installed
runtime versions and publishes:

- `<image>:astro-<astro-version>`
- `<image>:astro-<astro-version>-py<python-major.minor>-node<node-major.minor>-ms<mainsequence-version>`
- `<image>:latest`

The final published image is also labeled with exact full versions:

- `org.opencontainers.image.title=astro-<astro-version>`
- `org.opencontainers.image.description=Astro <astro-version> stream image for Main Sequence orchestration with Python <python-full-version>, Node <node-full-version>, and mainsequence <mainsequence-full-version>.`
- `org.opencontainers.image.version=<astro-version>`
- `org.opencontainers.image.mainsequence.version=<mainsequence-full-version>`
- `org.opencontainers.image.python.version=<python-full-version>`
- `org.opencontainers.image.node.version=<node-full-version>`

Before publishing a deployable image, bump the Astro project patch version locally and commit it:

```bash
npm run version:patch
```

After the version change is reviewed and committed, publish the image:

```bash
npm run build:gcp
```

Cloud Build reads the committed `package.json` version. It does not mutate git state or create
versions inside CI.

Provide production Main Sequence auth through your existing secret path:

- `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID`
- `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`

Only the runtime credential env vars are required for Main Sequence auth.

### Build-Time Note

This Cloud Build file intentionally does not set `MAINSEQUENCE_PIP_SPEC`.
The Dockerfile default `ARG MAINSEQUENCE_PIP_SPEC=mainsequence` is used so the build always pulls
the latest available `mainsequence` release at build time.

## Variables We Should Usually Omit At Runtime

These are migration-oriented local-dev variables and should normally be left out of the GCP
deployment:

- `ASTRO_LEGACY_REPO_STATE_DIR`
- `ASTRO_LEGACY_HOST_PI_AGENT_DIR`

They only make sense if we are doing a one-time import from an older runtime layout.

## Proposed `cloudbuild.yaml` Shape

This is the structure now used in `deployment/gcp/cloudbuild.yaml`:

```yaml
substitutions:
  _AR_REGION: europe-west1
  _AR_REPO: tsorm-images
  _IMAGE_NAME: astro/astro-pi-stream
  _DOCKER_TARGET: astro-pi-stream
  _IMAGE_PREFIX: ${_AR_REGION}-docker.pkg.dev/${PROJECT_ID}/${_AR_REPO}/${_IMAGE_NAME}
  _LATEST_IMAGE: ${_AR_REGION}-docker.pkg.dev/${PROJECT_ID}/${_AR_REPO}/${_IMAGE_NAME}:latest

steps:
  - id: build-image-and-detect-mainsequence-version
    name: gcr.io/cloud-builders/docker
    entrypoint: bash
    args:
      - -c
      - |
        LOCAL_IMAGE="astro-pi-stream:build-${BUILD_ID}"
        LABELED_IMAGE="astro-pi-stream:labeled-${BUILD_ID}"

        docker build \
          -f Dockerfile \
          --target ${_DOCKER_TARGET} \
          -t "$${LOCAL_IMAGE}" \
          .

        ASTRO_VERSION="$$(docker run --rm --entrypoint node "$${LOCAL_IMAGE}" \
          -p "require('/app/package.json').version")"
        MAINSEQUENCE_VERSION="$$(docker run --rm --entrypoint python "$${LOCAL_IMAGE}" \
          -c "import importlib.metadata as metadata; print(metadata.version('mainsequence'))")"
        PYTHON_VERSION_FULL="$$(docker run --rm --entrypoint python "$${LOCAL_IMAGE}" \
          -c "import platform; print(platform.python_version())")"
        PYTHON_VERSION_TAG="$$(docker run --rm --entrypoint python "$${LOCAL_IMAGE}" \
          -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")"
        NODE_VERSION_FULL="$$(docker run --rm --entrypoint node "$${LOCAL_IMAGE}" \
          -p "process.versions.node")"
        NODE_VERSION_TAG="$$(docker run --rm --entrypoint node "$${LOCAL_IMAGE}" \
          -p "process.versions.node.split('.').slice(0, 2).join('.')" )"

        VERSION_IMAGE="${_IMAGE_PREFIX}:astro-$${ASTRO_VERSION}"
        DESCRIPTIVE_IMAGE="${_IMAGE_PREFIX}:astro-$${ASTRO_VERSION}-py$${PYTHON_VERSION_TAG}-node$${NODE_VERSION_TAG}-ms$${MAINSEQUENCE_VERSION}"

        printf '%s' "$${VERSION_IMAGE}" > /workspace/version_image.txt
        printf '%s' "$${DESCRIPTIVE_IMAGE}" > /workspace/descriptive_image.txt

        CONTAINER_ID="$$(docker create "$${LOCAL_IMAGE}")"
        trap 'docker rm -f "$${CONTAINER_ID}" >/dev/null 2>&1 || true' EXIT

        docker commit \
          --change "LABEL org.opencontainers.image.title=\"astro-$${ASTRO_VERSION}\"" \
          --change "LABEL org.opencontainers.image.description=\"Astro $${ASTRO_VERSION} stream image for Main Sequence orchestration with Python $${PYTHON_VERSION_FULL}, Node $${NODE_VERSION_FULL}, and mainsequence $${MAINSEQUENCE_VERSION}.\"" \
          --change "LABEL org.opencontainers.image.version=$${ASTRO_VERSION}" \
          --change "LABEL org.opencontainers.image.mainsequence.version=$${MAINSEQUENCE_VERSION}" \
          --change "LABEL org.opencontainers.image.python.version=$${PYTHON_VERSION_FULL}" \
          --change "LABEL org.opencontainers.image.node.version=$${NODE_VERSION_FULL}" \
          "$${CONTAINER_ID}" \
          "$${LABELED_IMAGE}" >/dev/null

        docker tag "$${LABELED_IMAGE}" "$${VERSION_IMAGE}"
        docker tag "$${LABELED_IMAGE}" "$${DESCRIPTIVE_IMAGE}"
        docker tag "$${LABELED_IMAGE}" "${_LATEST_IMAGE}"

        docker rm -f "$${CONTAINER_ID}" >/dev/null 2>&1 || true
        trap - EXIT

  - id: push-version-image
    waitFor:
      - build-image-and-detect-mainsequence-version
    name: gcr.io/cloud-builders/docker
    entrypoint: bash
    args:
      - -c
      - |
        VERSION_IMAGE="$$(cat /workspace/version_image.txt)"
        docker push "$${VERSION_IMAGE}"

  - id: push-latest-image
    waitFor:
      - build-image-and-detect-mainsequence-version
    name: gcr.io/cloud-builders/docker
    args:
      - push
      - ${_LATEST_IMAGE}

  - id: push-descriptive-image
    waitFor:
      - build-image-and-detect-mainsequence-version
    name: gcr.io/cloud-builders/docker
    entrypoint: bash
    args:
      - -c
      - |
        DESCRIPTIVE_IMAGE="$$(cat /workspace/descriptive_image.txt)"
        docker push "$${DESCRIPTIVE_IMAGE}"

options:
  dynamicSubstitutions: true
  logging: CLOUD_LOGGING_ONLY
```

## Notes Before Implementation

- `.env` is excluded by `.dockerignore`, which is good and should stay that way
- the deploy target should use the `astro-pi-stream` Docker target, not `astro-pi`
- the running service still depends on durable storage at `/home/appuser/.astro-container-data`
- rebuilding without bumping the Astro package version will repoint that `astro-<version>` tag to
  the newly built image
- the extra `astro...-py...-node...-ms...` tag is still just another tag on the same built image,
  not a separate build
- the published tags now point at the same final labeled image, while the unlabeled local build
  image is only an internal intermediate used during Cloud Build
- if we want horizontal scaling later, we should think carefully about whether multiple replicas can
  safely share the same writable runtime volume
- if we need strict stateless deployment later, the app will need architectural changes around
  session storage, auth persistence, and runtime filesystem assumptions

## Next Step

The next step should be to replace the placeholder defaults with your real values for:

- `_AR_REGION`
- `_AR_REPO`
- `_IMAGE_NAME`

and then wire your build trigger or manual `gcloud builds submit` flow to use
`deployment/gcp/cloudbuild.yaml`.
