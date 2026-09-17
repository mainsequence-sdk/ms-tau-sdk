---
name: tau-local-development
description: Run and diagnose the installed Main Sequence TAU SDK locally while keeping conversational runtime state out of platform Agent and AgentSession records.
---

# TAU Local Development

Use this skill for local conversations, streaming checks, workspace debugging, and runtime
diagnosis. Local mode changes session persistence; it does not make Main Sequence or the model
provider offline.

## Required environment

Obtain the user's access and refresh JWTs through the supported Main Sequence authentication or
project-launcher flow. The SDK consumes the environment handoff directly and does not import or
invoke the `mainsequence` package:

```bash
export MAINSEQUENCE_AUTH_MODE=jwt
export MAINSEQUENCE_ACCESS_TOKEN="<user-access-token>"
export MAINSEQUENCE_REFRESH_TOKEN="<user-refresh-token>"
export TAU_LOCAL_MODE=true
export TAU_LOCAL_PROVIDER="<provider>"
export TAU_LOCAL_MODEL="<model>"
# Optional:
export TAU_LOCAL_THINKING="<thinking-level>"

uv run ms-tau
```

Set `MAINSEQUENCE_ENDPOINT` only for a non-default platform endpoint. Never place JWTs or provider
credentials in source control, `.tau`, copied skills, command output, or debugging artifacts.

`TAU_LOCAL_PROVIDER` and `TAU_LOCAL_MODEL` select an exact execution target but do not authorize
it. Main Sequence validates that target and supplies the provider-control evidence and credential.
The user JWT is used for Main Sequence only and is never sent to the model provider.

## Persistence boundary

Local conversations, entries, activity, cancellation, leases, and snapshots are stored in
workspace-scoped SQLite state below `TAU_LOCAL_STATE_ROOT`, which defaults to
`~/.tau/mainsequence`. Local mode does not create or update platform Agent or AgentSession runtime
state and never uploads local history when the mode changes.

Main Sequence authentication, provider hydration, inference, and MCP remain remote. MCP tool calls
can read or mutate real platform resources under the authenticated user's permissions.

## Checks

After startup, verify the process in this order:

1. `/health` reports `mode: local` and safe composition diagnostics.
2. `/ready` confirms user authentication, provider control, and MCP readiness.
3. `/version` reports the installed SDK version.
4. A chat request without `sessionUid` receives an effective local session identifier.
5. A second request with that identifier resumes the same conversation.
6. Streaming and cancellation operate without platform session persistence.

Agent-targeted responses, A2A discovery/dispatch, caller delivery, and other operations that
require a registered Agent or AgentSession return `local_mode_capability_unsupported`. That is a
capability boundary, not a reason to create hidden platform records.

## Failure classification

- Startup setting failure: inspect the exact missing or conflicting environment variable.
- Authentication failure: refresh or re-export the supported JWT pair; do not substitute a
  runtime credential in local mode.
- Provider/model rejection: verify the exact configured names against the authenticated live
  catalog; do not silently select a different model.
- MCP failure: distinguish catalog/connectivity failure from a server-side authorization failure.
- Session conflict: keep one provider/model/thinking selection for the existing local session or
  intentionally use a new local state namespace.
- Project behavior failure: inspect `.tau` resources and extension diagnostics using
  `tau-project-customization`.
