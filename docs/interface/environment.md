# Environment Variables

- `ASTRO_STREAM_HOST` (default `0.0.0.0`)
- `ASTRO_STREAM_PORT` (default `8787`)
- `ASTRO_STREAM_TRUSTED_ORIGINS` (comma-separated browser origins allowed for cross-origin requests, for example `http://localhost:5173,http://127.0.0.1:5173`)
- `ASTRO_STREAM_SESSION_DIR` (default `<repo>/.astro/stream-sessions`)
- `ASTRO_STREAM_LOG_TRAFFIC` (`0` disables logging)
- `ASTRO_STREAM_LOG_REQUEST_BODIES` (`1` enables request payload debug logging)
- `ASTRO_MAINSEQUENCE_CONFIG_DIR` (in containers use `/root/.astro-container-data/.config/mainsequence`)
- `PI_CODING_AGENT_DIR` (Pi runtime state directory; in containers use `/root/.astro-container-data/.pi/agent`)
- `ASTRO_CONTAINER_DATA_DIR` (durable container runtime root; in containers use `/root/.astro-container-data`)
- `ASTRO_LEGACY_REPO_STATE_DIR` (optional read-only legacy repo-local Astro state used only for one-time migration)
- `ASTRO_LEGACY_HOST_PI_AGENT_DIR` (optional read-only host Pi source used to merge `auth.json` and `sessions/` during one-time migration)
- `BUILD_AGENTS_IN_BACKEND` (enable backend agent registration)
- `OLLAMA_HOST` (optional Ollama host used by `GET /api/chat/get_available_models`, for example `http://localhost:11434`)

Auth-backed model providers also rely on their normal upstream env vars, for example:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_OAUTH_TOKEN`

`ASTRO_STREAM_CORS_ORIGIN` is now treated as a deprecated single-origin fallback for backward compatibility.
