# Environment Variables

- `ASTRO_STREAM_HOST` (default `0.0.0.0`)
- `ASTRO_STREAM_PORT` (default `8787`)
- `ASTRO_STREAM_TRUSTED_ORIGINS` (comma-separated browser origins allowed for cross-origin requests, for example `http://localhost:5173,http://127.0.0.1:5173`)
- `ASTRO_STREAM_SESSION_DIR` (default `<repo>/.astro/stream-sessions`; in containers use
  `/session-state/sessions`)
- `ASTRO_SESSION_STATE_DIR` (pod-local session state root; in containers use `/session-state`)
- `ASTRO_SESSION_OVERRIDES_DIR` (pod-local scoped Pi settings overlays; in containers use `/session-state/session-overrides`)
- `ASTRO_PROVIDER_CREDENTIAL_DIR` (pod-local scoped Pi auth dirs hydrated from backend-owned
  provider credentials; in containers use `/session-state/pi-agent-auth`)
- `ASTRO_PROVIDER_CREDENTIAL_FLUSH_INTERVAL_MS` (default `10000`; periodic safety flush interval
  for scoped provider credentials while Pi is running)
- `ASTRO_RELEASE_VERSION` (the Astro release/build version baked into the image and exposed for
  runtime debugging)
- `ASTRO_STREAM_LOG_TRAFFIC` (`0` disables logging)
- `ASTRO_STREAM_LOG_HEALTH_TRAFFIC` (`1` enables `GET /health` access logs; health probe access
  lines are suppressed by default)
- `ASTRO_STREAM_LOG_REQUEST_BODIES` (`1` enables request payload debug logging)
- `ASTRO_MAINSEQUENCE_CONFIG_DIR` (container-local Main Sequence CLI config; in containers use `/home/appuser/.astro-container-data/.config/mainsequence`)
- `PI_CODING_AGENT_DIR` (container-local Pi runtime state directory; in containers use `/home/appuser/.astro-container-data/.pi/agent`)
- `ASTRO_CONTAINER_DATA_DIR` (container-local rebuildable runtime root; in containers use `/home/appuser/.astro-container-data`)
- `ASTRO_ORCHESTRATOR_CWD` (optional writable cwd override for `astro-orchestrator`; defaults to `<ASTRO_CONTAINER_DATA_DIR>/astro-orchestrator-runtime`)
- `ASTRO_ORCHESTRATOR_PROJECT_PI_DIR` (optional writable project `.pi` override for `astro-orchestrator`; defaults to `<ASTRO_CONTAINER_DATA_DIR>/.pi/project`)
- `BUILD_AGENTS_IN_BACKEND` (enable backend-backed session start, hydration, and checkpoint coordination)
- `OLLAMA_HOST` (optional Ollama host used by `GET /api/chat/get_available_models`, for example `http://localhost:11434`)

## Remote project worker mode

These env vars are used by image-backed `mainsequence-project-executor` pods:

- `ASTRO_EXECUTION_MODE`
  - set this to `remote_project_worker` for image-backed executor pods
- `ASTRO_FIXED_AGENT_NAME`
  - recommended value: `mainsequence-project-executor`
- `ASTRO_FIXED_PROJECT_CWD`
  - fixed project path inside the image, for example `/usr/local/share/user-skel/app`
  - this tells Astro where the mounted or baked project lives for executor-mode work
- `ASTRO_PROJECT_IMAGE_REF`
  - optional image reference or digest persisted into project-session metadata

For the deployed Jupyter-based executor image, the effective runtime contract is:

- `HOME=/home/jovyan`
- `ASTRO_CONTAINER_DATA_DIR=/home/jovyan/.astro-container-data`
- `ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/jovyan/.astro-container-data/.config/mainsequence`
- `PI_CODING_AGENT_DIR=/home/jovyan/.astro-container-data/.pi/agent`
- `ASTRO_FIXED_PROJECT_CWD=/usr/local/share/user-skel/app`

The executor image is built to start under Kubernetes `runAsUser: 10000` / `runAsGroup: 10000`,
with Docker `WORKDIR /app`, and the startup command then changes into
`/usr/local/share/user-skel/app` before Astro starts so the live process cwd matches the real
project workspace.

For `mainsequence-project-executor`, incoming `/api/chat` and `/api/a2a/chat` requests do not need
to provide `projectId`. The streamer no longer tries to resolve `projectId` from the request path
for executor-mode requests and continues using the fixed project runtime even when request-side
`projectId` is absent.

For the full worker-image layout and pod contract, see
[`../components/remote-worker-image.md`](../components/remote-worker-image.md).

## Local mounted-project executor harness

These env vars are used by the `astro-project-executor` service in
[`docker-compose.yml`](../../docker-compose.yml):

- `A2A_DEV_PROJECT`
  - required for the local executor harness and local A2A debug mode
  - host path to mount into `/workspace/project` for the executor
  - Astro also uses the mounted project to read `.agents/agent_card.json` during local A2A discovery
- `ASTRO_EXECUTOR_STREAM_PORT`
  - optional host port for the local executor HTTP stream
  - defaults to `8790`

## Local A2A debug mode

- `A2A_DEV_PROJECT`
  - enables local A2A debug mode when set
  - in containers, Astro reads the mounted project path selected by `A2A_DEV_PROJECT` to mock A2A discovery from `.agents/agent_card.json`
- `A2A_DEV_BASE_URL`
  - optional direct base URL override for local A2A communication
  - when unset, Astro tries the local executor service URL and then `http://127.0.0.1:${ASTRO_EXECUTOR_STREAM_PORT:-8790}`

Auth-backed model providers also rely on their normal upstream env vars, for example:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_OAUTH_TOKEN`

`ASTRO_STREAM_CORS_ORIGIN` is now treated as a deprecated single-origin fallback for backward compatibility.
