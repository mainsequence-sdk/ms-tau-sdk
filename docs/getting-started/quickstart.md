# Quickstart

## Run Astro

```bash
npm run pi
```

The launch script loads `.env` from the repo root (if present) and prepares Main Sequence auth for
the configured auth mode.

## Run Astro over HTTP stream

```bash
npm run pi:stream
```

This starts the HTTP streaming wrapper on `http://0.0.0.0:8787` and is reachable on your LAN at `http://<your-ip>:8787`.

The stream endpoint accepts assistant-ui compatible `ui-message-stream` requests at `POST /api/chat` and returns SSE.
A compact JSON snapshot for an old conversation can be fetched later with
`GET /api/chat/history?sessionId=<runtime_session_id>`.

When backend agent registration is enabled, `runtime_session_id` is the backend `AgentSession.id`.
Active files live under the configured session directory, and durable continuity comes from backend
checkpoints. When backend registration is disabled, the local fallback key is `threadId`.

The request contract is latest-turn oriented:

- the backend must create the session before Astro is called
- `runtime_session_id` is required for every real non-mock streamed request
- `newChat` is deprecated as routing input and must not be relied on for session creation
- `messages` should contain only the exact user message just typed
- `threadId` is returned for client bookkeeping only and does not define continuity when agent registration is enabled
- `context` carries the current UI/app/surface metadata
- `tools` carries optional UI tool metadata

Every SSE chunk includes `agent_id`, and the response also includes `X-Agent-Id` when one was
returned by the backend for the thread.

## Build Docker images

Normal Pi image:

```bash
docker build --target astro-pi -t astro:pi .
```

If you omit `--target`, the default final image is the normal `astro-pi` launcher.

Set `MAINSEQUENCE_PIP_SPEC` in `.env` before building if you want a specific library version, for example:

```dotenv
MAINSEQUENCE_PIP_SPEC=mainsequence==0.1.2
```

HTTP stream image:

```bash
docker build --target astro-pi-stream -t astro:pi-stream .
```

Run normal Pi:

```bash
docker run --rm -it --env-file .env astro:pi
```

Run the stream server:

```bash
docker run --rm -it -p 8787:8787 --env-file .env astro:pi-stream
```

## Run with Docker Compose

Normal Pi:

```bash
docker compose run --rm astro-pi
```

HTTP stream:

```bash
docker compose up astro-pi-stream
```

Both available:

```bash
docker compose up astro-pi-stream
docker compose run --rm astro-pi
```

`astro-pi` uses the optional `pi-shell` profile, so a plain `docker compose up` brings up the stream
service plus the checkpoint sidecar by default. The standalone Pi container remains
available when you target it explicitly with `docker compose run --rm astro-pi`.

The compose file mounts:

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

It also sets:

- `ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/appuser/.astro-container-data/.config/mainsequence`
- `PI_CODING_AGENT_DIR=/home/appuser/.astro-container-data/.pi/agent`
- `ASTRO_STREAM_SESSION_DIR=/session-state/sessions`
- `ASTRO_SESSION_OVERRIDES_DIR=/session-state/session-overrides`
- `ASTRO_CHECKPOINT_HOLDER_ID=pod/local-compose-astro-pi-stream`
- `ASTRO_CONTAINER_DATA_DIR=/home/appuser/.astro-container-data`

At boot, Astro prepares container-local runtime state. Provider auth, provider signin state, and
stream session files have no host or repo-local source path in the container; backend-owned
checkpoints are the durable session source of truth. Container startup also removes stale Pi
provider auth/signin files from `PI_CODING_AGENT_DIR`.

At runtime, Astro runs as non-root `appuser`. Rebuildable runtime state lives under
`/home/appuser/.astro-container-data`; active session files live under `/session-state/sessions` and
are shared with the checkpoint sidecar.

That means:

- Astro code changes on the host are visible in the container without rebuilding the image
- restart the service after code edits with `docker compose restart astro-pi-stream`
- Pi runtime state lives under `/home/appuser/.astro-container-data/.pi/agent`
- the orchestrator project `.pi` copy lives under `/home/appuser/.astro-container-data/.pi/project`
- the orchestrator runs from `/home/appuser/.astro-container-data/astro-orchestrator-runtime`, not `/app`
- Main Sequence CLI auth lives under `/home/appuser/.astro-container-data/.config/mainsequence`
- stream session artifacts live under `/session-state/sessions`
- auth/session files have no host or repo-local source path into the container runtime
- `node_modules` stay container-local and Linux-native
- helper binaries such as `rg` persist under `/home/appuser/.astro-container-data/.pi/agent/bin`
- those helper binaries remain container-managed and Linux-native instead of being reused from the
  host's general Pi install

This launcher:

- installs local npm dependencies when needed
- checks for Node 20+
- verifies the `pi` CLI is available
- verifies the repo-installed `pi-web-access` package is present
- runs the TypeScript check
- starts `pi`

## Optional standalone tutorial verifier

Run Pi and use the `verify-mainsequence-tutorial` prompt template only when you explicitly want the fixed tutorial-regression workflow.

## Run only the local executor runtime

Use the local executor container harness when you want to inspect or test the dedicated project
runtime directly:

```bash
export A2A_DEV_PROJECT=/absolute/path/to/checked-out-project
export ASTRO_EXECUTOR_PROJECT_ID=<project-id>
docker compose up astro-project-executor
```

## Manual commands

```bash
npm run check
pi
```

## Container runtime

When running Astro in containers, use the repo-root `Dockerfile` targets `astro-pi` and `astro-pi-stream`.

Run Python commands inside this same app container (do not use a separate Python-only container).
The container image installs `mainsequence` from `MAINSEQUENCE_PIP_SPEC` in the final Docker layer. `.env` is not copied into the image, so pass env vars at runtime with `--env-file` or `-e`.

## Read next

- [`pi-primer.md`](./pi-primer.md)
- [`request-lifecycle.md`](./request-lifecycle.md)
- [`../components/prompts.md`](../components/prompts.md)
