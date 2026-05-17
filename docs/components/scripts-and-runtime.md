# Scripts and runtime

Astro keeps its repo-local runtime in TypeScript.

## Repo-local runtime

Astro's runtime lives in:

- `pi/extensions/hooks/*/index.ts`
- `pi/extensions/tools/*/index.ts`
- `pi/extensions/shared/*`
- `scripts/start_pi.mjs`
- `scripts/start_pi_stream.ts`

## Why TypeScript is the default

- Pi extensions are TypeScript entry points
- Astro's local logic is mostly wiring, routing, and file shaping
- avoiding a local language bridge keeps the repo easier to inspect

## What the scripts do

### `scripts/start_pi.mjs`

Starts Astro through the local package setup.

It loads `.env` from the repo root and prepares Main Sequence auth according to
`MAINSEQUENCE_AUTH_MODE`.
With `MAINSEQUENCE_AUTH_MODE=runtime_credential`, it validates
`MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` and `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`, exchanges the
runtime credential through the CLI, verifies the updated CLI auth state, and re-exchanges it on
the configured interval.

### `scripts/start_pi_stream.ts`

Starts the HTTP streaming interface wrapper around the Pi process.

The stream server loads `.env` on startup, binds the HTTP port, and keeps `GET /health`
independent from Main Sequence auth.
Before each non-mock `POST /api/chat` request, it runs the auth-mode-aware Main Sequence CLI gate
so the session does not begin from a stale unauthenticated CLI state.
Runtime credential mode updates the CLI auth store by re-exchanging the runtime credential.
Spawned agent processes inherit the runtime credential env and the shared CLI auth-store location.
They do not inherit token-style Main Sequence auth env vars, which prevents a long-running agent
process from overriding the runtime-managed auth store.
It accepts latest-turn UI requests, injects the optional UI `system`, `context`, and `tools`
metadata into the prompt, requires backend-owned `runtime_session_id` for real chat/A2A execution,
hydrates backend session authority when local/request metadata is insufficient, and uses backend
AgentSession id files (fallback to `threadId` when registration is disabled) for continuity.

Astro no longer bootstraps a dedicated project-coder runtime inside the normal chat stream.
Project implementation is owned by `mainsequence-project-executor`, and image-backed executor
pods are expected to start from an already-prepared project runtime instead of rebuilding one on
the hot path.
Structured operational logs from this runtime follow the contract documented in
[`../interface/logging.md`](../interface/logging.md).

## Container runtime note

The repo root `Dockerfile` is the deployable app image definition.

For the image-backed project executor runtime, this repo also ships
[`Dockerfile.remote-worker`](./remote-worker-image.md). That image keeps the prepared project
runtime from `BASE_IMAGE` and overlays Astro into `/app` so a remote pod can run
`mainsequence-project-executor` against the already-prepared project image.

The shared `astro-base` stage includes:

- Python 3.11
- Node 20 (for Pi)
- local npm dependencies
- the runtime folders Astro actually needs:
  - `.pi/`
  - `pi/`
  - `interface/`
  - `scripts/`

The final `astro-mainsequence` stage installs `mainsequence` from `MAINSEQUENCE_PIP_SPEC`, so SDK/version
changes only invalidate the tail end of the Docker build.

The Dockerfile exposes two runnable targets:

- `astro-pi`
  - starts normal Pi with `node scripts/start_pi.mjs`
- `astro-pi-stream`
  - starts the HTTP stream server with `tsx scripts/start_pi_stream.ts`
  - listens on port `8787`

The repo root `docker-compose.yml` wraps those targets as three services:

- `astro-pi`
  - interactive normal Pi
- `astro-pi-stream`
  - HTTP stream service
  - bind-mounts the editable Astro source paths into `/app` for live code iteration
  - mounts the tmpfs-backed `astro_session_emptydir` volume at `/session-state`
  - sets `ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/jovyan/.astro-container-data/.config/mainsequence`
  - sets `PI_CODING_AGENT_DIR=/home/jovyan/.astro-container-data/.pi/agent`
  - sets `ASTRO_STREAM_SESSION_DIR=/session-state/sessions`
  - sets `ASTRO_CONTAINER_DATA_DIR=/home/jovyan/.astro-container-data`
  - prepares container-local runtime state with no provider-auth source path from host or repo-local storage
  - prunes stale Pi provider auth/signin files from `PI_CODING_AGENT_DIR` before startup
  - writes a runtime-local `settings.json` into `/home/jovyan/.astro-container-data/.pi/agent`
  - materializes the repo-local `/app/.pi` project settings into `/home/jovyan/.astro-container-data/.pi/project`
  - runs `astro-orchestrator` from `/home/jovyan/.astro-container-data/astro-orchestrator-runtime`
    with `.pi` symlinked to the writable project settings copy
  - preserves Astro package sources like `/app` and `pi-web-access`
  - keeps `node_modules` container-local from the image layer
  - keeps active session files in the shared tmpfs-backed session volume instead of durable local storage
  - keeps helper binaries under `/home/jovyan/.astro-container-data/.pi/agent/bin`
  - runs as non-root `jovyan`
  - uses `/home/jovyan/.astro-container-data` as the container runtime root
- `astro-session-checkpoint-sidecar`
  - local Docker simulation of the Kubernetes checkpoint sidecar
  - mounts the same `astro_session_emptydir` volume at `/session-state`
  - watches manifest-backed session files under `/session-state`
  - debounces changes, detects compaction markers, and flushes complete checkpoint bundles to the
    backend

## Kubernetes deployment guidance

When deploying Astro in Kubernetes, keep session files pod-local and restore/flush them through the
backend checkpoint cycle:

- mount an `emptyDir` at `/session-state`
- do not rely on external SSH state mounted from outside the pod
- let the pod generate rebuildable repo SSH keys under `/home/jovyan/.astro-container-data/.ssh`
- let Astro run `astro-orchestrator` from `/home/jovyan/.astro-container-data/astro-orchestrator-runtime`, not `/app`
- set `ASTRO_STREAM_SESSION_DIR=/session-state/sessions`

Operational guidance:

- do not share one writable session filesystem across unrelated replicas
- keep `/home/jovyan/.astro-container-data/.ssh/known_hosts` writable inside the pod so first contact can be recorded with `accept-new`
- do not treat project implementation as a session switch inside the orchestrator container

When using containers, run Python commands inside this same app container (do not use a separate Python-only container).
The image intentionally does not copy `docs/`, `tutorial/`, or `.env`; provide env vars at container start.

## Related pages

- [`knowledge.md`](./knowledge.md)
- [`remote-worker-image.md`](./remote-worker-image.md)
- [`../getting-started/quickstart.md`](../getting-started/quickstart.md)
