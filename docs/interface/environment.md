# Environment

## Required

```dotenv
MAINSEQUENCE_BACKEND=http://api.main-sequence.app:8000
MAINSEQUENCE_AUTH_MODE=runtime_credential
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID=replace-with-coding-agent-service-credential-id
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET=replace-with-coding-agent-service-credential-secret
```

The credential pair must belong to the deployed coding-agent service and
authenticates Astro to Django REST and MCP. Organization-test and project
runtime credentials are not valid for MCP. Model-provider secrets are not
configured globally in Astro; Django hydrates them for the owning user and
session.

Project Executor Agent discovery requires no Environment variable. Django
derives the exact Environment from the authenticated coding-agent service and
its persisted ProjectBranch. Astro removes the selector from Tau-visible MCP
schemas and sends no replacement argument. An inconsistent or unscoped Project
Executor credential fails closed rather than falling back to production or
Organization-wide visibility. One Project Executor process serves one backend-
derived Environment and never multiplexes sessions across Environments.

## Service

```dotenv
ASTRO_HOST=0.0.0.0
ASTRO_PORT=8787
ASTRO_PROJECT_CWD=/workspace
ASTRO_HOME=/home/jovyan
ASTRO_A2A_ASSET_ROOT=/tmp/astro-a2a-assets
ASTRO_SESSION_ASSET_ROOT=/tmp/astro-session-assets
ASTRO_SESSION_IDLE_TTL_SECONDS=900
ASTRO_SESSION_LEASE_TTL_SECONDS=90
ASTRO_SESSION_LEASE_RENEW_SECONDS=30
ASTRO_TURN_TIMEOUT_SECONDS=900
ASTRO_SHUTDOWN_GRACE_SECONDS=30
ASTRO_MAX_TURN_OUTPUT_BYTES=4194304
ASTRO_LOG_LEVEL=INFO
ASTRO_LOG_MACHINE_SINK=true
ASTRO_LOG_HUMAN_SINK=false
ASTRO_LOG_PAYLOADS=false
```

Optional settings:

- `ASTRO_TRUSTED_ORIGINS`: comma-separated browser origins.
- `ASTRO_A2A_MAX_INLINE_FILE_BYTES`: maximum decoded inline A2A file size.
- `ASTRO_SESSION_EVICTION_INTERVAL_SECONDS`: idle-runtime scan interval.
- `ASTRO_BACKEND_*`: backend timeout and response-size limits.

`ASTRO_SESSION_LEASE_RENEW_SECONDS` must be lower than
`ASTRO_SESSION_LEASE_TTL_SECONDS`. At least one logging sink must be enabled.
Machine logs are structured JSON; human logs are concise text.
`ASTRO_LOG_PAYLOADS` is retained as a compatibility setting but does not enable
content capture. Prompts, prompt hashes, complete histories, model output, tool
payloads, credentials, and provider headers are never logged. Both sinks use
the same Structlog event pipeline as `tdag-django`; see
[logging](./logging.md).

## Web tools

The `packages/tau-web-access` workspace distribution can use:

- `EXA_API_KEY`
- `PERPLEXITY_API_KEY`
- `GEMINI_API_KEY`
- `GITHUB_TOKEN`

These are optional. Available search backends depend on which values are set.
