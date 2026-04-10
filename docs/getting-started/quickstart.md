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

Sessions are persisted per `threadId` under `.astro/stream-sessions` by default.

The request contract is latest-turn oriented:

- `messages` should contain only the exact user message just typed
- `threadId` carries conversation continuity
- `context` carries the current UI/app/surface metadata
- `tools` carries optional UI tool metadata

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

The compose file mounts:

- `${HOME}/.pi/agent` -> `/root/.pi/host-agent`
- `${HOME}/mainsequence` -> `/root/mainsequence`
- `${HOME}/mainsequence-dev` -> `/root/mainsequence-dev`

It also sets `PI_CODING_AGENT_DIR=/root/.pi/agent-runtime` and imports reusable host Pi state from
`/root/.pi/host-agent`.

That means:

- host `auth.json`, `settings.json`, and `sessions/` are reused in the container
- helper binaries such as `rg` stay container-local under `/root/.pi/agent-runtime/bin`
- Linux no longer reuses host-downloaded helper binaries from another OS or architecture

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
