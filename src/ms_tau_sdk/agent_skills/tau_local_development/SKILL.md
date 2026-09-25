---
name: tau-local-development
description: Run and diagnose Main Sequence TAU SDK local mode, including live provider and MCP access, workspace SQLite persistence, and public local A2A Message and Task workflows.
---

# TAU Local Development

Use this skill when developing a repository that embeds `ms-tau-sdk` without creating platform
Agent or AgentSession runtime records. Local mode changes where runtime state is stored. It is not
an offline, mock-provider, or mock-MCP mode.

## Start the process

Obtain the user's access and refresh JWTs through the supported Main Sequence login or
project-launcher flow. The SDK consumes that environment handoff directly; it does not install,
import, or invoke the `mainsequence` Python package and does not read its private credential store.

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

Set `MAINSEQUENCE_ENDPOINT` only for a non-default platform endpoint. Local mode binds to
`127.0.0.1:8787` by default. Treat an explicit public bind as privileged exposure: each accepted
request can use the authenticated user's live Main Sequence permissions.

Never place JWTs or provider credentials in source control, `.tau`, copied skills, command output,
or debugging artifacts. The user JWT is sent only to Main Sequence. Main Sequence validates the
explicit `TAU_LOCAL_PROVIDER` and `TAU_LOCAL_MODEL`, returns provider-control evidence, and hydrates
the provider credential used for inference. Provider secrets are neither environment settings nor
local state.

## Exact local/remote boundary

| Concern | Local-mode behavior |
| --- | --- |
| Agent or AgentSession registration | Never created or updated |
| Chat history, snapshots, leases, activity, and cancellation | Workspace SQLite |
| Public A2A Message and Task state | Workspace SQLite |
| Main Sequence authentication and token refresh | Remote, using the exported JWT pair |
| Provider authorization, hydration, and inference | Remote and real |
| Main Sequence MCP catalog and tool calls | Remote and real; platform mutations remain possible |
| Project instructions, skills, hooks, and extensions | Normal repository `.tau` composition |

The database defaults to `~/.tau/mainsequence/<workspace-hash>/runtime.sqlite3`; override its
parent only with `TAU_LOCAL_STATE_ROOT`. Local state is never uploaded or attached to a platform
AgentSession when local mode is disabled. Stop all processes for the workspace before moving or
deleting its workspace-hash directory.

Local operational events are also written as structured JSON Lines to
`~/.tau/mainsequence/<workspace-hash>/logs/tau.jsonl` (or beneath `TAU_LOCAL_STATE_ROOT`). The
file sink is mandatory in local mode even when console logging is enabled. Read the current file
and its five rotated backups when diagnosing startup, provider, MCP, A2A, or project-extension
failures. The SDK log does not capture arbitrary extension `print()` output; project-owned code
must use propagating Python logging and must not log secrets or prompt/tool payloads. A failure to
open the file stops startup; the logs are never uploaded to an AgentSession.

## Public local A2A contract

An A2A protocol Task is local coordination state, not a platform `AgentTask`, and does not require
a platform AgentSession. Local mode supports:

- REST `POST /api/a2a/v1/message:send` and JSON-RPC `message/send` for Message or Task execution;
- REST `POST /api/a2a/v1/message:stream` and JSON-RPC `message/stream` for SSE Task execution;
- REST `GET /api/a2a/v1/tasks`, `GET /api/a2a/v1/tasks/{task_id}`,
  `POST /api/a2a/v1/tasks/{task_id}:cancel`, and
  `GET /api/a2a/v1/tasks/{task_id}:subscribe`;
- JSON-RPC `tasks/list`, `tasks/get`, `tasks/cancel`, and `tasks/subscribe`;
- subscription resumption with `afterSequence`;
- continuation of a Task in `input_required` or `auth_required` by sending a new Message carrying
  that `taskId`; and
- Task status, messages, artifacts, attempts, and events surviving process restart.

To request a Task from `message:send`, send the response-kind extension header
`A2A-Extensions: https://mainsequence.ai/a2a/extensions/response-kind/v1` together with
`configuration.responseKind: "task"`. Omit that selection for the default completed Message
response. `message:stream` is always Task-oriented and does not accept `responseKind` or
`returnImmediately`.

Incoming local A2A requests do not need managed-gateway `X-Caller-*` headers. TAU assigns
workspace-local provenance and maps each supplied `contextId` into the same workspace-scoped local
session namespace used by chat. A supplied `taskId` remains the public Task identifier. The local
`GET /api/a2a/v1/extendedAgentCard` response advertises Message, Task, and streaming support while
disabling push notifications.

The following remain unavailable because they require registered platform routing or callback
identity:

- platform discovery of the unregistered local process;
- `/internal/a2a` backend dispatch and caller-delivery hooks;
- A2A push-notification configuration; and
- `resume_caller` Task completion.

These are the only A2A-adjacent capability boundaries; public local Message and Task requests must not return
`local_mode_capability_unsupported`.

Each standard A2A Task carries the time its current state was recorded in
`task.status.timestamp`. For `TASK_STATE_COMPLETED`, that is the completion time. The A2A Task
object has no standard top-level creation timestamp; Tau persists `created_at` in local SQLite,
and Tau Board joins that local row when it displays the A2A Task list. Do not invent extra A2A
wire fields for Board presentation.

## Inspect and test with Tau Board

Install `ms-tau-sdk[tau-board]`, run `tau-board` as a separate process, and connect it to the
loopback local-mode Tau endpoint. The Agent tab requires an already loaded session: first send a
Chat or A2A request, then select that session. It shows the effective Agent Card, loaded tools by
source, project extension diagnostics, and registered project extension entry source.

Only project-extension tools with an object JSON Schema are runnable in the workbench. Select the
tool, fill its generated form or raw JSON, validate the exact canonical arguments, acknowledge
the side-effect warning, then run it. Validation never calls the tool. Run invokes the exact
loaded tool without a model call and without adding chat entries, A2A Tasks, or artifacts. The
tool still has the Tau process's real filesystem, network, environment, and credential access;
there is no automatic rollback. A busy session, stale catalog, expired confirmation, non-project
tool, oversized output, timeout, or cancellation must fail explicitly.

## Outbound A2A through Main Sequence MCP

Keep the projected `a2a.send_message` tool available. A local agent may send a Message or create a
Task for a deployed Agent using authenticated-user semantics. For a Task, use
`completion_policy: "poll"` and use the returned Task handle with `a2a.wait_task`. Local mode rejects
`completion_policy: "resume_caller"` because it has no registered callback target and never
fabricates caller-session proof. Other live MCP tools remain available and can read or mutate real
platform resources under the authenticated user's permissions.

## Verify the runtime

Check the process in this order:

1. `GET /health` reports `mode: local` and safe composition diagnostics.
2. `GET /ready` confirms user authentication, provider control, local storage, and MCP readiness.
3. `GET /version` reports the expected installed SDK release.
4. A chat request without `sessionUid` returns an effective identifier in
   `X-Agent-Session-Uid`; reuse it to resume the conversation.
5. A REST `POST /api/a2a/v1/message:send` without gateway caller headers returns a Message.
6. Repeat it with the response-kind extension and `configuration.responseKind: "task"`; verify
   the Task through `GET /api/a2a/v1/tasks/{task_id}` and after a process restart.
7. Verify `POST /api/a2a/v1/message:stream`, cancellation, subscription, and continuation for the
   project workflow being developed.

## Classify failures

- Startup setting failure: inspect the exact missing or conflicting environment variable.
- Authentication failure: refresh or re-export the JWT pair; do not substitute a runtime
  credential in local mode.
- Provider/model rejection: verify the exact configured names against the authenticated live
  catalog; do not silently select a different model.
- MCP failure: distinguish catalog/connectivity failure from server-side authorization. A tool
  genuinely requiring caller AgentSession proof must fail explicitly without causing registration.
- Session conflict: keep one provider/model/thinking selection for an existing local session or
  intentionally use a new local state namespace.
- Public local A2A capability error: treat it as a stale or defective SDK. Capability errors are
  valid only at the unsupported routing/callback boundaries listed above.
- Project behavior failure: inspect `.tau` resources and extension diagnostics using
  `tau-project-customization`.
