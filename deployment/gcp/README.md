# GCP Deployment Plan

## Recommendation

This repo is not a purely stateless web container. The runtime expects durable local state under:

- `/root/.astro-container-data`

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

The current `deployment/gcp/cloudbuild.yaml` has three responsibilities:

1. Build the `astro-pi-stream` image from the repo `Dockerfile`
2. Detect which `mainsequence` version was actually installed in the image
3. Push two tags to Artifact Registry:
   one tag for the detected library version and one `latest` tag

Suggested high-level flow:

```yaml
steps:
  - name: Build runtime image
    uses: docker build
    notes:
      - target should be astro-pi-stream
      - rely on the Dockerfile default so `mainsequence` resolves to latest

  - name: Detect installed mainsequence version
    uses: docker run + python importlib.metadata

  - name: Push version tag
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

## Runtime Env For The Existing GKE Workload

Cloud Build should not manage these values.
They belong to the already-deployed GKE workload outside this repo's image publication pipeline.

### Values That Should Stay Fixed At Runtime

These are container contract values from the repo and should usually stay fixed in the running
service instead of varying per environment:

- `HOME=/root`
- `ASTRO_STREAM_HOST=0.0.0.0`
- `ASTRO_CONTAINER_DATA_DIR=/root/.astro-container-data`
- `ASTRO_MAINSEQUENCE_CONFIG_DIR=/root/.astro-container-data/.config/mainsequence`
- `PI_CODING_AGENT_DIR=/root/.astro-container-data/.pi/agent`
- `ASTRO_STREAM_SESSION_DIR=/root/.astro-container-data/.astro/stream-sessions`

These come directly from the Dockerfile, compose file, and persistent-state docs.

### Values That Should Come From Secrets, Not Cloud Build Substitutions

Do not put these in `cloudbuild.yaml` substitutions:

- `MAINSEQUENCE_ACCESS_TOKEN`
- `MAINSEQUENCE_REFRESH_TOKEN`
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

- `MAINSEQUENCE_BACKEND`
- `MAINSEQUENCE_PROJECTS_BASE`
- `MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS`
- `BUILD_AGENTS_IN_BACKEND`
- `ASTRO_STREAM_PORT`
- `ASTRO_STREAM_TRUSTED_ORIGINS`
- `OLLAMA_HOST`

### Concrete Split For The Current Env

For the values currently shown in local `.env`, the split is:

Set these on the existing GKE workload when the pod is created or updated:

```env
MAINSEQUENCE_BACKEND=http://192.168.1.111:8000
MAINSEQUENCE_PROJECTS_BASE=mainsequence-dev
MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS=600
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
latest available `mainsequence`, then Cloud Build detects the installed version and publishes both:

- `<image>:<mainsequence-version>`
- `<image>:latest`

If the running GKE workload also needs auth at runtime, provide these through your existing secret
path or persisted runtime storage:

- `MAINSEQUENCE_ACCESS_TOKEN`
- `MAINSEQUENCE_REFRESH_TOKEN`

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
  _AR_REPO: astro
  _IMAGE_NAME: astro-pi-stream
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

        docker build \
          -f Dockerfile \
          --target ${_DOCKER_TARGET} \
          -t "$${LOCAL_IMAGE}" \
          .

        MAINSEQUENCE_VERSION="$$(docker run --rm --entrypoint python "$${LOCAL_IMAGE}" \
          -c "import importlib.metadata as metadata; print(metadata.version('mainsequence'))")"

        VERSION_IMAGE="${_IMAGE_PREFIX}:$${MAINSEQUENCE_VERSION}"

        printf '%s' "$${VERSION_IMAGE}" > /workspace/version_image.txt
        docker tag "$${LOCAL_IMAGE}" "$${VERSION_IMAGE}"
        docker tag "$${LOCAL_IMAGE}" "${_LATEST_IMAGE}"

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

options:
  dynamicSubstitutions: true
  logging: CLOUD_LOGGING_ONLY
```

## Notes Before Implementation

- `.env` is excluded by `.dockerignore`, which is good and should stay that way
- the deploy target should use the `astro-pi-stream` Docker target, not `astro-pi`
- the running service still depends on durable storage at `/root/.astro-container-data`
- rebuilding while `mainsequence` stays on the same version will repoint that version tag to the
  newly built image
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
