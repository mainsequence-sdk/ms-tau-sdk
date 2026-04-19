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

It loads `.env` from the repo root, refreshes the Main Sequence access token, runs a blocking
`mainsequence login --access-token ...` bootstrap before Pi starts, and then starts the Main
Sequence token refresh loop when
`MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS` is set.

### `scripts/start_pi_stream.ts`

Starts the HTTP streaming interface wrapper around the Pi process.

The stream server loads `.env` on startup, refreshes the Main Sequence access token, runs the same
blocking Main Sequence CLI login bootstrap, and then uses the same token refresh interval if
configured.
Before each non-mock `POST /api/chat` request, it re-runs that deterministic CLI login gate so the
session does not begin from a stale `Not logged in` CLI state.
It accepts latest-turn UI requests, injects the optional UI `system`, `context`, and `tools`
metadata into the prompt, treats `newChat: true` as a UI hint for a new conversation, registers
the backend Agent when enabled, and uses backend AgentSession id files (fallback to `threadId` when
registration is disabled) for continuity.

Before a new `mainsequence-project-coder` session begins normal Pi work, the stream runtime deterministically:

- runs `mainsequence project sdk-status --path . --json`
- runs `mainsequence project build_local_venv --path .`
- runs `uv sync`
- verifies `.venv`
- starts the coder session with `VIRTUAL_ENV` set to that `.venv` and the venv `bin/` prepended to `PATH`

Those steps are emitted to the frontend as synthetic tool events on the coder session itself.
When the coder session is created through `session_switch`, the same stream response continues as
that coder session so the bootstrap is visible immediately instead of waiting for another user turn.
The resulting SDK/runtime snapshot is persisted in local session metadata and injected into the
coder prompt as runtime context before Pi starts.

### `scripts/mainsequence_project_set_up_locally.ts`

This is Astro's hardened wrapper around `mainsequence project set-up-locally`.

Inside Astro, the orchestrator should call:

```bash
tsx /app/scripts/mainsequence_project_set_up_locally.ts <id>
```

instead of calling raw `mainsequence project set-up-locally <id>` directly.

The wrapper:

- bootstraps Astro's persistent SSH runtime first
- creates a project-scoped checkout home under `/root/.astro-container-data/project-checkout-runtime/project-<id>/home`
- keeps the checkout SSH key and `known_hosts` inside that project-scoped home so keys are not reused only by repo slug
- symlinks the project-scoped home back to the shared Main Sequence CLI config under `/root/.astro-container-data/.config/mainsequence`
- relies on SSH `StrictHostKeyChecking=accept-new` with a pod-local persistent `known_hosts`
- retries bounded transient clone failures such as host-key verification or delayed deploy-key access

## Container runtime note

The repo root `Dockerfile` is the deployable app image definition.

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

The repo root `docker-compose.yml` wraps those targets as two services:

- `astro-pi`
  - interactive normal Pi
- `astro-pi-stream`
  - HTTP stream service
  - bind-mounts the editable Astro source paths into `/app` for live code iteration
  - bind-mounts `./.astro` to `/app/.astro-migration-source` only as a one-time migration source
  - mounts `${HOME}/.pi/agent` to `/root/.pi/host-agent` only so migration can merge `auth.json`
    and `sessions/`
  - mounts named volume `astro_container_data` to `/root/.astro-container-data`
  - sets `ASTRO_MAINSEQUENCE_CONFIG_DIR=/root/.astro-container-data/.config/mainsequence`
  - sets `PI_CODING_AGENT_DIR=/root/.astro-container-data/.pi/agent`
  - sets `ASTRO_STREAM_SESSION_DIR=/root/.astro-container-data/.astro/stream-sessions`
  - sets `ASTRO_CONTAINER_DATA_DIR=/root/.astro-container-data`
  - optionally honors `ASTRO_STORAGE_SIM_TOTAL_BYTES` so `GET /api/storage/usage` can simulate a fixed PVC capacity during local development
  - migrates legacy repo-local runtime state into the volume once, then merges `auth.json` and
    `sessions/` from the host Pi source and stops reimporting it
  - writes a runtime-local `settings.json` into `/root/.astro-container-data/.pi/agent`
  - preserves Astro package sources like `/app` and `pi-web-access`
  - keeps `node_modules` container-local from the image layer
  - keeps durable runtime state under the named volume instead of the repo-local `.astro/` tree
  - keeps helper binaries under `/root/.astro-container-data/.pi/agent/bin`
  - symlinks `/root/.ssh`, `/root/.pi/agent`, `/root/.config/mainsequence`, `/root/.astro/stream-sessions`, `/root/mainsequence`, `/root/mainsequence-dev`, and `/root/.local/share/uv` into subdirectories of `/root/.astro-container-data`

## Kubernetes deployment guidance

When deploying Astro in Kubernetes, keep the same runtime shape as local Docker:

- mount one durable volume at `/root/.astro-container-data`
- do not rely on host-mounted `~/.ssh`
- let the pod generate and persist its own repo SSH keys under `/root/.astro-container-data/.ssh`
- let Astro's checkout wrapper generate per-project SSH identities under `/root/.astro-container-data/project-checkout-runtime/project-<id>/home/.ssh`
- let the same volume persist `known_hosts`, Main Sequence config, stream sessions, and project checkouts
- use the same `tsx /app/scripts/mainsequence_project_set_up_locally.ts <id>` wrapper in production pods

Operational guidance:

- prefer one PVC per Astro runtime instance instead of sharing one writable `.ssh` state across unrelated replicas
- keep `/root/.astro-container-data/.ssh/known_hosts` writable so first contact can be recorded with `accept-new`
- do not hand off to `mainsequence-project-coder` until local setup succeeds and the project reports `is_initialized=true`

When using containers, run Python commands inside this same app container (do not use a separate Python-only container).
The image intentionally does not copy `docs/`, `tutorial/`, or `.env`; provide env vars at container start.

## Related pages

- [`knowledge.md`](./knowledge.md)
- [`../getting-started/quickstart.md`](../getting-started/quickstart.md)
