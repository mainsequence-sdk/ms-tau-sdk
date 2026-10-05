# Troubleshooting

## Workspace validation fails

Run the command from the project root or set `MAINSEQUENCE_TAU_WORKSPACE` to an existing readable
directory. Files, missing paths, and inaccessible directories are rejected before runtime startup.

## Runtime authentication fails

In managed mode, verify `MAINSEQUENCE_AUTH_MODE=runtime_credential`,
`MAINSEQUENCE_RUNTIME_CREDENTIAL_ID`, and one proof of that credential:
`MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE` or `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`. With the
token file set, the secret is not used. Do not substitute a user token. The
`runtime.auth.exchange.completed` log event names the proof that was sent in `proof`
(`workload_identity_token` or `credential_secret`), never its value. Each failure has its own
message:

- The token file is missing, unreadable, or empty. The message names the file and the reason.
  Check that the deployment mounts the projected token at the path in
  `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE`. The SDK does not fall back to the secret.
- The exchange was rejected with HTTP 401. The platform did not accept the token or the secret, and
  it answers every failed verification with the same generic 401. The SDK does not retry it.
- The exchange failed with HTTP 429 or 503 after four attempts, or the platform asked to retry after
  more than 60 seconds. The platform throttled the exchange or could not verify the credential for
  the moment. A process that failed at startup must be started again; a running process exchanges
  again on its next request.

In local mode, verify `MAINSEQUENCE_AUTH_MODE=jwt` and the explicit
`TAU_LOCAL_PROVIDER`/`TAU_LOCAL_MODEL` selection. `mainsequence_auth_source` in `/health` names
where the access token comes from: `cli`, `environment`, or `env_file`.

With no token variable set, local mode asks the Main Sequence CLI for the token. Each failure has
its own message:

- No CLI was found. Install a Main Sequence CLI that has `mainsequence auth token`, or set
  `MAINSEQUENCE_CLI` to its path, or provide the token pair.
- The CLI has no usable session. Run `mainsequence login` for the backend in
  `MAINSEQUENCE_ENDPOINT`. A running process uses the new session on its next request. A process
  that failed at startup must be started again.
- The CLI does not know `auth token`. It is too old. Upgrade it.
- The machine has no credential store. Provide the token pair in the environment.
- The CLI answered for another backend. `MAINSEQUENCE_ENDPOINT` and the CLI session must name the
  same backend.
- The CLI did not answer in 15 seconds, or its output was not the expected JSON. Run
  `mainsequence auth token --json > /dev/null; echo $?` with the same `MAINSEQUENCE_ENDPOINT` to
  see its message and exit code without printing the token.

With `MAINSEQUENCE_ACCESS_TOKEN` and `MAINSEQUENCE_REFRESH_TOKEN` both set, local mode uses that
pair and never runs the CLI. The launcher must export both before `ms-tau` starts. One without the
other is a startup error. When the backend rejects the pair, export a fresh one. When startup warns
that tokens were read from the project `.env`, run `mainsequence refresh-token` in that directory
to remove them.

The TAU SDK deliberately does not import the `mainsequence` package or read its private auth
store. Backend URL, response status, and safe error detail may be logged; credential values are
always redacted.

## A hosted runtime refuses requests or does not start

A runtime is hosted when `MAINSEQUENCE_CALLER_AUTH_MODE=assertion` or any of `APP_NAME`,
`FASTAPI_PUBLIC_BASE_URL`, `MAINSEQUENCE_CALLER_ASSERTION_ISSUER` or
`MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL` is set. It then admits a request only with the platform's
signed assertion in `X-MainSequence-Caller-Assertion`. The `request_identity.rejected` log event
names the assertion the route required (`caller` or `platform`) and the reason, never the token.

- **401 `A valid caller assertion is required.`** The request did not come through the platform, or
  its assertion is missing, expired, duplicated, for another release or Environment, or a platform
  assertion. `X-User-UID` and the other gateway headers are not accepted in its place. This includes
  `/health`, `/ready`, and `/version`; platform probes use the launcher's `/ms-health-deployment`.
- **401 `A valid platform assertion is required.`** A request to `/internal/*` did not carry the
  platform's own assertion. Only the platform calls these routes.
- **503 `Caller authentication is unavailable.`** The runtime could not fetch the platform's key set
  from `MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL`. It retries on the next request that needs the keys.
- **403 `Only the session's owner or an Organization admin can address this session.`** The caller
  is authenticated, but the session, or the session of the Task, was created by another User and
  the caller is not an Organization admin.
- **The application does not start.** `create_app()` names each hosting setting that is missing or
  invalid: the issuer, an HTTPS key-set URL, and `APP_NAME` and
  `MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID` as canonical lowercase UUIDs. Local mode refuses to
  start while any of these settings makes the runtime hosted; unset them for local development.
- **The platform launcher refuses the application.** The launcher reads
  `app.state.mainsequence_request_identity` from the application object it serves. Serve the
  application `create_app()` returns; an application that mounts it inside another one declares
  nothing.

## Local provider hydration is rejected

Local mode requires the Main Sequence backend's authenticated-user hydration contract. The
request intentionally contains no Agent or AgentSession UID. A rejection can mean that the
provider/model is unauthorized, the user's stored provider credential is unavailable, or the
backend environment has not deployed that contract. The SDK never falls back to an environment
provider API key.

## Reset or inspect local conversations

Health reports the workspace digest. Local state lives under
`~/.tau/mainsequence/<workspace-hash>/runtime.sqlite3` unless `TAU_LOCAL_STATE_ROOT` is set. Stop
all `ms-tau` processes for that workspace before moving or deleting its workspace-hash directory.
Removing local state cannot remove a platform AgentSession because local state is never attached
to one.

## A local chat keeps running after a reload or returns `session_busy`

A local `/api/chat` turn runs to its end even when the page that started it reloads or closes. Its
session shows `working: true` in `GET /api/local/v1/chat-sessions`, and its history returns the
running turn in `inProgressMessage`. While it runs, `POST /api/chat` for the same session returns
409 `session_busy`: wait for `working` to clear, or stop the turn with
`POST /api/chat/session/cancel`. A session a stopped process left mid-turn stops reporting
`working` when its lease expires.

## Inspect local operational logs

Local-mode structured events are appended to
`~/.tau/mainsequence/<workspace-hash>/logs/tau.jsonl`, beside the workspace's SQLite state. Use
`TAU_LOCAL_STATE_ROOT` to relocate the parent of both. Each line is a JSON event; rotated backups
are kept in the same `logs` directory. If the log file cannot be opened, local startup fails with
the path instead of silently discarding durable diagnostics. The file excludes raw exception
messages and known secret-bearing fields, but project extensions must also avoid logging their
own sensitive content. Do not commit or upload the local state directory.

For an OpenAI-compatible provider failure, inspect the human-readable incident in Tau Board before
the raw JSON. The incident shows the concrete HTTPX type, transport phase, attempts, total provider
duration, failure UID, and bounded traceback frames when the SDK could observe them. A diagnostic
that explicitly says the dependency did not expose its cause must not be guessed into a timeout.
`ms-tau-sdk` temporarily restores the missing Tau 0.4.2 terminal diagnostic under ADR 0016; the
patch never records credentials, headers, prompts, response bodies, or Python locals.

## Tau runtime state cannot be written

Both modes keep durable Tau runtime state — built-in extension state, provider credentials, project
trust, agent-call diagnostics — under `MAINSEQUENCE_TAU_STATE_ROOT/<workspace-hash>`, defaulting to
`$XDG_STATE_HOME/ms-tau-sdk` or `~/.local/state/ms-tau-sdk`. A container with a read-only root
filesystem or no writable home fails there. Point `MAINSEQUENCE_TAU_STATE_ROOT` at a writable
volume. Never point it inside the installed package: `ms_tau_sdk/resources/` is a read-only input
that lives in site-packages.

## Local A2A returns a capability error

Public `/api/a2a` Message and Task routes are supported in local mode. A capability error is valid
only for `/internal/a2a` backend delivery hooks, platform discovery of
the unregistered local process, push notifications, or `resume_caller`. For local Task completion,
use polling. Incoming local calls do not need managed-gateway `X-Caller-*` headers. If a public
Message or Task route returns this error, the running SDK is stale.

## An A2A Task remains submitted or working

Check `a2a_task_recovery` and `a2a_task_policy` in `/health`, then inspect the Task in Tau Board.
Local mode scans for recovery at startup and on the configured interval. A submitted Task is
rescheduled from its durable requester Message; repeated safe-start failures eventually produce
`failed` with `recovery_exhausted`. A working Task is not stale while its owner still holds a live
session lease. After ownership and the stale interval expire, an uncheckpointed attempt becomes
`failed` with `ambiguous_execution_outcome` so project or MCP side effects are not duplicated.

In managed mode, dispatch, lease expiry, retry, and exhausted-recovery terminalization belong to
the Main Sequence backend. `task_terminalization_unknown` means TAU observed execution failure but
could not prove that its terminal settlement was stored. Retrieve or subscribe to the Task instead
of assuming either success or failure from the disconnected request.

## Task history, result, or execution looks wrong

First identify the missing ontology. Requester/responder communication belongs to `Task.history`;
agent output belongs to `artifacts`; model/tool activity belongs to the correlated Tau turn; and
mutation/replay evidence belongs to Task events and logs. Repeated `output_updated` events normally
mean revisions of one streaming Artifact, not repeated responses.

Use `historyLength=0` to prove a client is not requesting history, or a positive bound to inspect
the latest Message tail. Omission requests 100. Public payloads must use `ROLE_USER`/`ROLE_AGENT`;
local persistence uses `ROLE_REQUESTER`/`ROLE_RESPONDER`. A legacy `{code, message}` status object,
object-valued `extensions`, or mismatched Task/context ID is rejected by design.

In Tau Board, compare the attempt's turn UID and half-open entry interval with Execution. A pending
resolution after lease loss means Task recovery must commit or abandon the reservation before a
replacement runtime can load the Session. Do not clear SQLite lease/turn fields manually: that can
detach entries from their Task or hide an ambiguous external side effect.

## An MCP tool fails only in local mode

Main Sequence MCP remains connected with user JWT authentication. Tools marked as requiring a real
caller AgentSession proof are still visible, but local mode does not fabricate that proof. Such a
tool may return a typed server-side capability/authorization failure. Other MCP tools and resources
remain live and may mutate real platform resources.

## A project extension fails

Extension diagnostics include a project-relative path, extension name, severity, safe error type,
and effective catalog counts. Reproduce in the project's locked environment. The project owns the
extension and its optional dependencies; the SDK does not install or repair them dynamically.

## Health is ready but no Main Sequence tools appear

Check `mcp_tool_count`, `mcp_resource_count`, and startup dependency settings. Disabling startup
dependencies is intended for isolated tests; normal authenticated startup connects MCP before
readiness.

## A durable turn conflicts or loses its lease

The SDK fails closed on sequence, lease, or provider-evidence conflicts. Use the session and turn
identifiers from structured logs; do not retry with stale local state or modify backend records from
the project process.
