# Container Quickstart

Astro runs only as a container. The service requires a Python 3.13 image and does
not install or launch Node.js.

1. Create `.env` from `.env.example` and replace the runtime credential
   placeholders.
2. Ensure Django is reachable on host port `8000`.
3. Start Astro:

```bash
docker compose up --build astro
```

Compose builds from the Astro monorepo root. The `tau-file-tools` and
`tau-web-access` distributions are workspace packages under `packages/`. It
publishes Astro on port `8787` and points
`MAINSEQUENCE_BACKEND` to `http://host.docker.internal:8000` by default.

Check the service:

```bash
curl http://localhost:8787/health
```

Use `ASTRO_CODE_REPOSITORY_PATH` to mount a different code repository at `/workspace`.
