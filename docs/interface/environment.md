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
- `ASTRO_STREAM_LOG_TRAFFIC` (`0` disables logging)
- `ASTRO_STREAM_LOG_REQUEST_BODIES` (`1` enables request payload debug logging)
- `ASTRO_MAINSEQUENCE_CONFIG_DIR` (container-local Main Sequence CLI config; in containers use `/home/appuser/.astro-container-data/.config/mainsequence`)
- `PI_CODING_AGENT_DIR` (container-local Pi runtime state directory; in containers use `/home/appuser/.astro-container-data/.pi/agent`)
- `ASTRO_CONTAINER_DATA_DIR` (container-local rebuildable runtime root; in containers use `/home/appuser/.astro-container-data`)
- `ASTRO_ORCHESTRATOR_CWD` (optional writable cwd override for `astro-orchestrator`; defaults to `<ASTRO_CONTAINER_DATA_DIR>/astro-orchestrator-runtime`)
- `ASTRO_ORCHESTRATOR_PROJECT_PI_DIR` (optional writable project `.pi` override for `astro-orchestrator`; defaults to `<ASTRO_CONTAINER_DATA_DIR>/.pi/project`)
- `BUILD_AGENTS_IN_BACKEND` (enable backend agent registration)
- `OLLAMA_HOST` (optional Ollama host used by `GET /api/chat/get_available_models`, for example `http://localhost:11434`)

Auth-backed model providers also rely on their normal upstream env vars, for example:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_OAUTH_TOKEN`

`ASTRO_STREAM_CORS_ORIGIN` is now treated as a deprecated single-origin fallback for backward compatibility.
