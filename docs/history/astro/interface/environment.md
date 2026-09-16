# Environment

## Required

```dotenv
MAINSEQUENCE_BACKEND=http://api.main-sequence.app:8000
MAINSEQUENCE_AUTH_MODE=runtime_credential
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID=replace-with-coding-agent-service-credential-id
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET=replace-with-coding-agent-service-credential-secret
```

The credential pair must belong to the deployed coding-agent service and
authenticates Astro to Django REST and MCP. Organization-test and code repository
runtime credentials are not valid for MCP. Model-provider secrets are not
configured globally in Astro; Django hydrates them for the owning user and
session.

CodeRepository Executor Agent discovery requires no Environment variable. Django
derives the exact Environment from the authenticated coding-agent service and
its persisted CodeRepositoryBranch. Astro removes the selector from Tau-visible MCP
schemas and sends no replacement argument. An inconsistent or unscoped CodeRepository
Executor credential fails closed rather than falling back to production or
Organization-wide visibility. One CodeRepository Executor process serves one backend-
derived Environment and never multiplexes sessions across Environments.

## Service

```dotenv
ASTRO_HOST=0.0.0.0
ASTRO_PORT=8787
ASTRO_CODE_REPOSITORY_CWD=/workspace
ASTRO_HOME=/home/appuser
ASTRO_A2A_ASSET_ROOT=/tmp/astro-a2a-assets
ASTRO_SESSION_IDLE_TTL_SECONDS=900
ASTRO_SESSION_LEASE_TTL_SECONDS=90
ASTRO_SESSION_LEASE_RENEW_SECONDS=30
ASTRO_TURN_TIMEOUT_SECONDS=900
ASTRO_SHUTDOWN_GRACE_SECONDS=30
ASTRO_MAX_TURN_OUTPUT_BYTES=4194304
ASTRO_A2A_TASK_OUTPUT_FLUSH_INTERVAL_MS=200
ASTRO_A2A_TASK_OUTPUT_FLUSH_BYTES=8192
ASTRO_A2A_TASK_EVENT_POLL_SECONDS=0.5
ASTRO_LOG_LEVEL=INFO
ASTRO_LOG_MACHINE_SINK=true
ASTRO_LOG_HUMAN_SINK=false
ASTRO_LOG_PAYLOADS=false
```

Container deployments use the lean Python runtime ABI: `APP_HOME`, `HOME`,
and `ASTRO_HOME` are `/home/appuser`, the single active Python environment is
`/opt/venv`, the CodeRepository checkout is `/workspace`, the immutable Astro
executor bundle is `/app`, and mutable executor session state is
`/session-state`. Runtime images and Kubernetes examples execute as
UID/GID `10000:10000`.

Optional settings:

- `ASTRO_TRUSTED_ORIGINS`: comma-separated browser origins.
- `ASTRO_A2A_MAX_INLINE_FILE_BYTES`: maximum decoded inline A2A file size.
- `ASTRO_A2A_TASK_OUTPUT_FLUSH_INTERVAL_MS`: maximum active-output coalescing interval.
- `ASTRO_A2A_TASK_OUTPUT_FLUSH_BYTES`: output bytes that trigger a durable artifact flush.
- `ASTRO_A2A_TASK_EVENT_POLL_SECONDS`: durable Task event/cancellation polling interval.
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

## Project extension configuration

Astro defines no environment variables for optional file-search, web-access, browser, media, or
other project tools. A CodeRepository that installs a `.tau/extensions` capability owns its
dependencies, credentials, egress policy, and configuration names.
