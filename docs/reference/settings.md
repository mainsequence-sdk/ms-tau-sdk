# Settings and Credentials

Settings are case-sensitive. SDK-specific names generally use `MAINSEQUENCE_TAU_*`; the two tool
composition settings use `TAU_*` so managed workflow environment validation accepts them.
Established Main Sequence connection and credential names remain unprefixed by the SDK product name.

## Managed authenticated startup

| Environment variable | Meaning |
| --- | --- |
| `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` | Runtime credential identifier. |
| `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE` | Path of the file that holds the runtime's projected workload identity token. When set, the token proves the credential and the secret is not used. An empty value is the same as unset. |
| `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET` | One process's runtime credential secret. Not needed when the token file is set. |

Managed mode uses `MAINSEQUENCE_AUTH_MODE=runtime_credential` and remains the default.
`MAINSEQUENCE_ENDPOINT` defaults to `https://api.main-sequence.app`.

The SDK exchanges the runtime credential for short-lived access tokens at
`POST /api/v1/runtime-credentials/token/`. Every exchange sends `credential_id` and exactly one
proof:

- **Workload identity token.** With `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE` set, every exchange
  reads the file again, because the token in it is rotated, and sends its content as
  `workload_identity_token`. The SDK never reads or sends the secret in this mode, and startup does
  not require it. A missing, unreadable, or empty file is a configuration error that names the file.
  The exchange never falls back to the secret. Main Sequence deploys a runtime in this mode with
  `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE=/var/run/secrets/mainsequence.io/runtime-identity/token`
  and no `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`.
- **Bootstrap secret.** Without the token file, every exchange sends
  `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET` as `credential_secret`.

The token stays inside the exchange. The settings hold only the path of its file. The SDK does not
copy the token into the environment, a file, a log, an error message, or anything it hands to
project code.

The settings never show a credential. `repr()` and `str()` of `TauSDKSettings`, and every log
event that carries it, leave out `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET` and mask the local
token pair. A settings validation error names the variable and the problem, and never repeats a
configured value.

The exchange is sent again, up to three times, when the platform answers HTTP 429 (throttled) or
503 (verification temporarily unavailable). The SDK waits as long as `Retry-After` asks, up to 60
seconds, and 1, 2, then 4 seconds when the answer has no usable `Retry-After`. A `Retry-After`
longer than 60 seconds ends the exchange with an error instead of a wait. HTTP 401 means the
platform did not accept the proof: the exchange fails at once, without a retry and without trying
another proof.

## Hosted caller authentication

The platform sets these variables when it hosts the runtime. Their names are the platform's.

| Environment variable | Meaning |
| --- | --- |
| `MAINSEQUENCE_CALLER_AUTH_MODE` | `assertion` makes the runtime hosted. `local` or an empty value leaves the decision to the variables below. Any other value is a settings error. |
| `APP_NAME` | The release UID, a canonical lowercase UUID. Setting it makes the runtime hosted. |
| `FASTAPI_PUBLIC_BASE_URL` | The release's public URL. Setting it makes the runtime hosted. |
| `MAINSEQUENCE_CALLER_ASSERTION_ISSUER` | The issuer every assertion names in `iss`. Setting it makes the runtime hosted. |
| `MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL` | The HTTPS URL of the platform's public key set. Setting it makes the runtime hosted. |
| `MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID` | The Organization Environment UID, a canonical lowercase UUID. |

This is the platform launcher's own rule: the runtime is hosted when
`MAINSEQUENCE_CALLER_AUTH_MODE=assertion` or when any of `APP_NAME`, `FASTAPI_PUBLIC_BASE_URL`,
`MAINSEQUENCE_CALLER_ASSERTION_ISSUER` or `MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL` is set.
`TauSDKSettings.request_identity_mode` reports `assertion` for a hosted runtime and `local`
otherwise. A hosted runtime verifies the platform's signed assertion on every request; see the
[runtime contract](./runtime-contract.md#request-identity).

A hosted runtime needs the issuer, an HTTPS key-set URL, and both UIDs. `create_app()` fails with
a configuration error that names each one that is missing or invalid. The key set is fetched from
the platform with the `MAINSEQUENCE_TAU_BACKEND_*_TIMEOUT_SECONDS` timeouts and the
`MAINSEQUENCE_TAU_BACKEND_MAX_RESPONSE_BYTES` limit. Like every setting, these variables are also
read from the project `.env`; the platform's values in the environment take precedence.

Do not set them for local development. Local mode refuses to start when they make the runtime
hosted.

## Authenticated local development

| Environment variable | Meaning |
| --- | --- |
| `TAU_LOCAL_MODE` | Set to `true` to keep Tau runtime state local. |
| `MAINSEQUENCE_AUTH_MODE` | Must be `jwt` in local mode. |
| `MAINSEQUENCE_CLI` | Optional path to the Main Sequence CLI that local mode asks for an access token. |
| `MAINSEQUENCE_ACCESS_TOKEN` | User access JWT handed to the process by a launcher or CI. Alternative to the CLI session. |
| `MAINSEQUENCE_REFRESH_TOKEN` | User refresh JWT of that pair, used through the public refresh endpoint. |
| `TAU_LOCAL_PROVIDER` | Required exact provider selection; contains no secret. |
| `TAU_LOCAL_MODEL` | Required exact model selection; contains no secret. |
| `TAU_LOCAL_THINKING` | Optional Tau thinking level. |
| `TAU_LOCAL_STATE_ROOT` | Local state root; defaults to `~/.tau/mainsequence`. |
| `TAU_LOCAL_A2A_TASK_RECONCILE_INTERVAL_SECONDS` | Local Task recovery scan interval; default `5`. |
| `TAU_LOCAL_A2A_TASK_STALE_AFTER_SECONDS` | Age after which an unowned working attempt is stale; default `120`. |
| `TAU_LOCAL_A2A_TASK_PENDING_TIMEOUT_SECONDS` | Maximum age for a Task that cannot be started; default `300`. |
| `TAU_LOCAL_A2A_TASK_MAX_RECOVERY_ATTEMPTS` | Deferred local start attempts before terminal failure; default `3`. |

The user's JWT authenticates Main Sequence provider hydration and MCP. It is never sent to the
selected model provider. Provider credentials are hydrated remotely and kept out of the
environment and local database. The SDK does not depend on, import, or dynamically load the
`mainsequence` Python package, and it does not read the Main Sequence CLI's credential store.

### Local credential source

Local mode takes the user's access token from one of two sources. The token variables decide
which one.

| Token variables | Source | `mainsequence_auth_source` in `/health` |
| --- | --- | --- |
| Neither is set | The Main Sequence CLI session | `cli` |
| Both are set in the process environment | The token pair | `environment` |
| Both are set and read from the project `.env` | The token pair; deprecated | `env_file` |
| Exactly one is set | None. Startup fails and names the missing variable. | |

**Main Sequence CLI session.** Log in once with `mainsequence login`. No token is exported and
none is written to `.env`. The SDK runs `mainsequence auth token --json` as a separate process and
keeps the returned access token in memory. It reuses the token until 60 seconds before it expires
and runs the command again after a rejected request. The command has 15 seconds to answer. The CLI
is the first of these that exists:

1. the file named by `MAINSEQUENCE_CLI`. A path that is not an existing file is a startup error;
2. `mainsequence` in the directory of the Python interpreter that runs `ms-tau`;
3. `mainsequence` on `PATH`.

The SDK passes its own `MAINSEQUENCE_ENDPOINT` to the command, so the CLI answers for the backend
that Tau uses, and it refuses an answer for another backend.

| The CLI reports | Meaning and remedy |
| --- | --- |
| Exit code 1 | No usable session for that backend. Run `mainsequence login`. |
| Exit code 2 | The CLI is too old to know `auth token`. Upgrade it, or provide the token pair. |
| Exit code 3 | The machine has no credential store. Provide the token pair in the environment. |
| No answer in 15 seconds | The command is stopped and the request fails. |
| Output that is not the JSON answer | The CLI and the SDK do not match. Upgrade the CLI. |

**Token pair.** Launchers and CI export `MAINSEQUENCE_ACCESS_TOKEN` and
`MAINSEQUENCE_REFRESH_TOKEN`. With both set, the SDK sends the access token, refreshes it through
the public refresh endpoint, and never runs the CLI. A pair read from the project `.env` file
still works and is deprecated: startup logs one warning that names the file and the two variables.
Run `mainsequence refresh-token` in that directory to remove the token lines.

Local mode and managed authentication are mutually exclusive. Local startup fails if the provider
or the model setting is absent, if only one token is set, or if no token is set and no Main
Sequence CLI is found. Managed startup fails if `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` is absent, or
if neither `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE` nor `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`
is set.

## Process and workspace

| Environment variable | Default |
| --- | --- |
| `MAINSEQUENCE_TAU_WORKSPACE` | Current directory |
| `MAINSEQUENCE_TAU_HOST` | `0.0.0.0` managed; `127.0.0.1` local unless explicitly set |
| `MAINSEQUENCE_TAU_PORT` | `8787` |
| `MAINSEQUENCE_TAU_TRUSTED_ORIGINS` | Empty; comma-separated CORS origins, which can also read the `X-Agent-Session-Uid` and `x-vercel-ai-ui-message-stream` chat headers |
| `MAINSEQUENCE_TAU_STARTUP_DEPENDENCIES_ENABLED` | `true` |
| `TAU_EXCLUDE_BASE_TOOLS` | `false`; when `true`, replace `read`, `write`, `edit`, and `bash` with a `read` that serves only skill files |
| `TAU_EXCLUDE_MAINSEQUENCE_MCP` | `false`; skip Main Sequence MCP connection, tools, resources, and resource prompt when `true` |
| `MAINSEQUENCE_TAU_STATE_ROOT` | `$XDG_STATE_HOME/ms-tau-sdk`, else `~/.local/state/ms-tau-sdk` |

The workspace must already exist, be a directory, and be readable. There is no no-workspace mode or
runtime-role selector.

The two exclusion settings are independent, process-wide, and apply to managed and local sessions.
With both `true`, the model sees only project `.tau/extensions` tools and the always-present
`task_request_input` and `task_request_authorization` tools for its own A2A Task. With either or
both `false`, the corresponding coding tools or Main Sequence MCP tools remain available. Project
tools compose in every mode. In a managed workflow, set the variables in
`harness_agent.spec.env_vars`; local `ms-tau` reads them from its process environment. Explicit
`TauSDKSettings` values can configure Python applications. Agent Card skills and system prompts do
not remove executable tools. Excluding the coding tools does not sandbox project extension code.

Both modes keep durable Tau runtime state — built-in extension state, provider credentials, project
trust, agent-call diagnostics — under `MAINSEQUENCE_TAU_STATE_ROOT/<workspace-hash>`. It is never
written into the installed package: `ms_tau_sdk/resources/` lives in site-packages, which is
read-only on a hardened install and must not mutate itself at runtime. Point
`MAINSEQUENCE_TAU_STATE_ROOT` at a writable volume when the home directory is not one. This root is
independent of `TAU_LOCAL_STATE_ROOT`, which only holds the local-mode database and log.

## Limits and lifecycle

| Environment variable | Default |
| --- | --- |
| `MAINSEQUENCE_TAU_BACKEND_CONNECT_TIMEOUT_SECONDS` | `10` |
| `MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS` | `60` |
| `MAINSEQUENCE_TAU_BACKEND_WRITE_TIMEOUT_SECONDS` | `60` |
| `MAINSEQUENCE_TAU_BACKEND_POOL_TIMEOUT_SECONDS` | `10` |
| `MAINSEQUENCE_TAU_PROVIDER_TIMEOUT_SECONDS` | `60`; HTTP timeout for model-provider calls, which bounds time-to-first-token on providers that send nothing before it |
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
| `MAINSEQUENCE_TAU_A2A_TASK_WAIT_TIMEOUT_SECONDS` | `30`; bounds request waiting but does not cancel the Task |

The local reconciler is the recovery owner for SQLite-backed Tasks. It safely schedules unclaimed
`submitted` work, expires stale attempts after their session lease is no longer active, and marks
uncertain external effects failed/ambiguous instead of replaying them. Lowering these values makes
development failure detection faster but can exhaust recovery during a temporary provider or
workspace outage.

## Logging

| Environment variable | Default |
| --- | --- |
| `MAINSEQUENCE_TAU_LOG_LEVEL` | `INFO` |
| `MAINSEQUENCE_TAU_LOG_MACHINE_SINK` | `true` |
| `MAINSEQUENCE_TAU_LOG_HUMAN_SINK` | `false` |
| `MAINSEQUENCE_TAU_LOG_PAYLOADS` | `false` |

At least one logging sink must be enabled. Payload content and secrets remain redacted regardless of
the payload setting.

Local mode additionally writes structured JSON Lines to
`~/.tau/mainsequence/<workspace-hash>/logs/tau.jsonl`, under `TAU_LOCAL_STATE_ROOT` when set. The
file sink is mandatory and independent of the console sink settings. It rotates at 10 MiB with
five backups. Managed mode does not create this file.
