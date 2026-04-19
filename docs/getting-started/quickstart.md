# Quickstart

## Run Astro

```bash
npm run pi
```

The launch script loads `.env` from the repo root (if present) and starts the Main Sequence token
refresh loop when `MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS` is set.

## Run Astro over HTTP stream

```bash
npm run pi:stream
```

This starts the HTTP streaming wrapper on `http://0.0.0.0:8787` and is reachable on your LAN at `http://<your-ip>:8787`.

The stream endpoint accepts assistant-ui compatible `ui-message-stream` requests at `POST /api/chat` and returns SSE.
A compact JSON snapshot for an old conversation can be fetched later with
`GET /api/chat/history?sessionId=<runtime_session_id>`.

Sessions are persisted per backend agent unique id plus a session suffix under `.astro/stream-sessions`
when agent registration is enabled (fallback to `threadId` when disabled).

The request contract is latest-turn oriented:

- `newChat: true` is treated as a UI hint for a new conversation
- `runtime_session_id`, when present, always resumes that exact session even if the request still says `newChat: true`
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

`astro-pi` uses the optional `pi-shell` profile, so a plain `docker compose up` brings up only the
stream service by default. The standalone Pi container remains available when you target it
explicitly with `docker compose run --rm astro-pi`.

The compose file mounts:

- `./.astro` -> `/app/.astro-migration-source` (read-only migration source)
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

It also sets:

- `ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/appuser/.astro-container-data/.config/mainsequence`
- `PI_CODING_AGENT_DIR=/home/appuser/.astro-container-data/.pi/agent`
- `ASTRO_STREAM_SESSION_DIR=/home/appuser/.astro-container-data/.astro/stream-sessions`

At first boot, Astro migrates legacy repo-local runtime state into the volume once, then merges
`auth.json` plus `sessions/` from the read-only host Pi source, and keeps using the volume as the
only durable runtime source of truth.

At runtime, Astro runs as non-root `appuser`, and the only valid durable runtime root is
`/home/appuser/.astro-container-data`.

That means:

- Astro code changes on the host are visible in the container without rebuilding the image
- restart the service after code edits with `docker compose restart astro-pi-stream`
- the active durable runtime state now lives inside the named volume instead of `./.astro`
- Pi runtime state lives under `/home/appuser/.astro-container-data/.pi/agent`
- Main Sequence CLI auth lives under `/home/appuser/.astro-container-data/.config/mainsequence`
- stream session artifacts live under `/home/appuser/.astro-container-data/.astro/stream-sessions`
- the host mounts are used only as one-time migration sources
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

## Run only `mainsequence-project-coder`

Use this only when the project has already been selected and checked out locally, and you already know the checked-out path plus the Main Sequence project id.

Interactive single-specialist mode:

```bash
npm run specialist -- --agent mainsequence-project-coder --cwd /absolute/path/to/checked-out-project --project-id <project-id>
```

Single task mode:

```bash
npm run specialist -- --agent mainsequence-project-coder --cwd /absolute/path/to/checked-out-project --project-id <project-id> "Read the project's task and status context, then implement the next task"
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
