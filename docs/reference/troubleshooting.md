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

## Local A2A returns a capability error

Public `/api/a2a` Message and Task routes are supported in local mode. A capability error is valid
only for `/internal/a2a` backend delivery hooks, platform discovery of
the unregistered local process, push notifications, or `resume_caller`. For local Task completion,
use polling. Incoming local calls do not need managed-gateway `X-Caller-*` headers. If a public
Message or Task route returns this error, the running SDK is stale.

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
