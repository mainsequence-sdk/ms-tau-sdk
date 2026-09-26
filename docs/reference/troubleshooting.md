# Troubleshooting

## Workspace validation fails

Run the command from the project root or set `MAINSEQUENCE_TAU_WORKSPACE` to an existing readable
directory. Files, missing paths, and inaccessible directories are rejected before runtime startup.

## Runtime authentication fails

In managed mode, verify both runtime credential variables are present and
`MAINSEQUENCE_AUTH_MODE=runtime_credential`. Do not substitute a user token.

In local mode, verify `MAINSEQUENCE_AUTH_MODE=jwt`, the access/refresh JWT variables, and the
explicit `TAU_LOCAL_PROVIDER`/`TAU_LOCAL_MODEL` selection. The Main Sequence login or launcher must
export the JWT pair before `ms-tau` starts. The TAU SDK deliberately does not import the
`mainsequence` package or read its private auth store. Backend URL, response status, and safe error
detail may be logged; credential values are always redacted.

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

## Inspect local operational logs

Local-mode structured events are appended to
`~/.tau/mainsequence/<workspace-hash>/logs/tau.jsonl`, beside the workspace's SQLite state. Use
`TAU_LOCAL_STATE_ROOT` to relocate the parent of both. Each line is a JSON event; rotated backups
are kept in the same `logs` directory. If the log file cannot be opened, local startup fails with
the path instead of silently discarding durable diagnostics. The file excludes raw exception
messages and known secret-bearing fields, but project extensions must also avoid logging their
own sensitive content. Do not commit or upload the local state directory.

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
