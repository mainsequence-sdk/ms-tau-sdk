# Settings and Credentials

Settings are case-sensitive. SDK-specific names use `MAINSEQUENCE_TAU_*`; established Main Sequence
connection and credential names remain unprefixed by the SDK product name.

## Managed authenticated startup

| Environment variable | Meaning |
| --- | --- |
| `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` | Runtime credential identifier. |
| `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET` | One process's runtime credential secret. |

Managed mode uses `MAINSEQUENCE_AUTH_MODE=runtime_credential` and remains the default.
`MAINSEQUENCE_ENDPOINT` defaults to `https://api.main-sequence.app`.

## Authenticated local development

| Environment variable | Meaning |
| --- | --- |
| `TAU_LOCAL_MODE` | Set to `true` to keep Tau runtime state local. |
| `MAINSEQUENCE_AUTH_MODE` | Must be `jwt` in local mode. |
| `MAINSEQUENCE_ACCESS_TOKEN` | User access JWT exported by the normal Main Sequence login flow. |
| `MAINSEQUENCE_REFRESH_TOKEN` | User refresh JWT used through the public refresh endpoint. |
| `TAU_LOCAL_PROVIDER` | Required exact provider selection; contains no secret. |
| `TAU_LOCAL_MODEL` | Required exact model selection; contains no secret. |
| `TAU_LOCAL_THINKING` | Optional Tau thinking level. |
| `TAU_LOCAL_STATE_ROOT` | Local state root; defaults to `~/.tau/mainsequence`. |

The SDK consumes the JWT environment handoff directly. It does not depend on, import, dynamically
load, or call the `mainsequence` Python package/CLI. The JWT authenticates Main Sequence provider
hydration and MCP; it is never sent to the selected model provider. Provider credentials are
hydrated remotely and kept out of the environment and local database.

Local mode and managed authentication are mutually exclusive. Local startup fails if any JWT,
provider, or model setting is absent; managed startup fails if either runtime credential setting is
absent.

## Process and workspace

| Environment variable | Default |
| --- | --- |
| `MAINSEQUENCE_TAU_WORKSPACE` | Current directory |
| `MAINSEQUENCE_TAU_HOST` | `0.0.0.0` managed; `127.0.0.1` local unless explicitly set |
| `MAINSEQUENCE_TAU_PORT` | `8787` |
| `MAINSEQUENCE_TAU_TRUSTED_ORIGINS` | Empty |
| `MAINSEQUENCE_TAU_STARTUP_DEPENDENCIES_ENABLED` | `true` |

The workspace must already exist, be a directory, and be readable. There is no no-workspace mode or
runtime-role selector.

## Limits and lifecycle

| Environment variable | Default |
| --- | --- |
| `MAINSEQUENCE_TAU_BACKEND_CONNECT_TIMEOUT_SECONDS` | `10` |
| `MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS` | `60` |
| `MAINSEQUENCE_TAU_BACKEND_WRITE_TIMEOUT_SECONDS` | `60` |
| `MAINSEQUENCE_TAU_BACKEND_POOL_TIMEOUT_SECONDS` | `10` |
| `MAINSEQUENCE_TAU_BACKEND_MAX_RESPONSE_BYTES` | `10485760` |
| `MAINSEQUENCE_TAU_MCP_READ_CONCURRENCY` | `8` |
| `MAINSEQUENCE_TAU_SESSION_ENTRY_BATCH_MAX_ENTRIES` | `100` |
| `MAINSEQUENCE_TAU_SESSION_ENTRY_BATCH_MAX_BYTES` | `8388608` |
| `MAINSEQUENCE_TAU_SESSION_LEASE_TTL_SECONDS` | `90` |
| `MAINSEQUENCE_TAU_SESSION_LEASE_RENEW_SECONDS` | `30` |
| `MAINSEQUENCE_TAU_SESSION_IDLE_TTL_SECONDS` | `900` |
| `MAINSEQUENCE_TAU_SESSION_EVICTION_INTERVAL_SECONDS` | `30` |
| `MAINSEQUENCE_TAU_TURN_TIMEOUT_SECONDS` | `900` |
| `MAINSEQUENCE_TAU_SHUTDOWN_GRACE_SECONDS` | `30` |
| `MAINSEQUENCE_TAU_MAX_TURN_OUTPUT_BYTES` | `4194304` |

The lease-renew interval must be lower than the lease TTL.

## A2A assets and streaming

| Environment variable | Default |
| --- | --- |
| `MAINSEQUENCE_TAU_A2A_ASSET_ROOT` | `/tmp/ms-tau-a2a-assets` |
| `MAINSEQUENCE_TAU_A2A_MAX_INLINE_FILE_BYTES` | `20971520` |
| `MAINSEQUENCE_TAU_A2A_MAX_AGGREGATE_FILE_BYTES` | `41943040` |
| `MAINSEQUENCE_TAU_A2A_MAX_INLINE_FILE_COUNT` | `8` |
| `MAINSEQUENCE_TAU_A2A_TASK_OUTPUT_FLUSH_INTERVAL_MS` | `200` |
| `MAINSEQUENCE_TAU_A2A_TASK_OUTPUT_FLUSH_BYTES` | `8192` |
| `MAINSEQUENCE_TAU_A2A_TASK_EVENT_POLL_SECONDS` | `0.5` |

## Logging

| Environment variable | Default |
| --- | --- |
| `MAINSEQUENCE_TAU_LOG_LEVEL` | `INFO` |
| `MAINSEQUENCE_TAU_LOG_MACHINE_SINK` | `true` |
| `MAINSEQUENCE_TAU_LOG_HUMAN_SINK` | `false` |
| `MAINSEQUENCE_TAU_LOG_PAYLOADS` | `false` |

At least one logging sink must be enabled. Payload content and secrets remain redacted regardless of
the payload setting.
