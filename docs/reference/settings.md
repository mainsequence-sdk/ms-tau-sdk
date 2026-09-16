# Settings and Credentials

Settings are case-sensitive. SDK-specific names use `MAINSEQUENCE_TAU_*`; established Main Sequence
connection and credential names remain unprefixed by the SDK product name.

## Required for authenticated startup

| Environment variable | Meaning |
| --- | --- |
| `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` | Runtime credential identifier. |
| `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET` | One process's runtime credential secret. |

`MAINSEQUENCE_AUTH_MODE` accepts only `runtime_credential`. `MAINSEQUENCE_BACKEND` defaults to
`https://api.main-sequence.app`.

## Process and workspace

| Environment variable | Default |
| --- | --- |
| `MAINSEQUENCE_TAU_WORKSPACE` | Current directory |
| `MAINSEQUENCE_TAU_HOST` | `0.0.0.0` |
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

## A2A, response assets, and streaming

| Environment variable | Default |
| --- | --- |
| `MAINSEQUENCE_TAU_A2A_ASSET_ROOT` | `/tmp/ms-tau-a2a-assets` |
| `MAINSEQUENCE_TAU_SESSIONLESS_ASSET_ROOT` | `/tmp/ms-tau-sessionless-assets` |
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
