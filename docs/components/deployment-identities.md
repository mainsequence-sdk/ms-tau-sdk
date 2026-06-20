# Runtime Context And Backend Identity

Astro has one internal Pi runtime shape. It can run without a project attached, or it can run inside
a prepared project cwd.

Main Sequence backend identities such as `astro-orchestrator` and `project-executor` still exist,
but they are backend/session metadata. They do not select separate Astro runtime architectures.

Do not introduce a second public name such as `project_worker`. `ASTRO_EXECUTION_MODE` may still
exist as legacy topology metadata for old worker images, but new deployments should not rely on it
for local runtime behavior.

## Contract Matrix

| Runtime context | Selected by | Working root | Backend `agentType` | Sidecar path contract |
| --- | --- | --- | --- | --- |
| No project attached | no `ASTRO_FIXED_PROJECT_CWD` | writable Astro runtime cwd | usually `astro-orchestrator`, but backend-owned | shared `/session-state`, `/home/jovyan` data roots |
| Project attached | `ASTRO_FIXED_PROJECT_CWD=<prepared project path>` | prepared project cwd | `astro-orchestrator`, `project-executor`, or adapter identity | shared `/session-state`, `/home/jovyan` data roots |

The important distinction is the fixed cwd. Backend identity remains useful for Main Sequence
session ownership, analytics, routing, and product policy.

## Identity Rules

- Backend/session identity is `Agent.agent_type`.
- Current Main Sequence identity values are `astro-orchestrator` and `project-executor`.
- `ASTRO_FIXED_AGENT_TYPE`, when set, pins backend/session identity.
- `ASTRO_FIXED_PROJECT_CWD`, when set, attaches the runtime to a prepared project workspace.
- Fixed backend identity validation still rejects mismatched request/session `agentType` before Pi
  launch.
- `ASTRO_EXECUTION_MODE=remote_project_worker` may remain in old deployments, but it must not be
  used as the local runtime selector.

## No-Project Runtime

This is the normal user-facing stream shape when no prepared project cwd is attached.

Selection:

- `ASTRO_FIXED_PROJECT_CWD` is unset.
- Requests provide or hydrate a backend `agentType`, usually `astro-orchestrator`.

Runtime behavior:

- runs from the writable Astro runtime cwd prepared at startup
- uses Astro Core and configured Pi package resources
- can create/select projects or communicate with project-attached sessions through backend-owned
  sessions and A2A

Container shape:

- built from the repo root [`Dockerfile`](../../Dockerfile) target
  `astro-mainsequence-pi-stream`
- paired with the `astro-mainsequence-session-checkpoint-sidecar` target from the same image
- main container and sidecar must share `/session-state`

## Project-Attached Runtime

This is the same Astro stream runtime started inside a prepared project workspace.

Selection:

- `ASTRO_FIXED_PROJECT_CWD=<prepared project path>`
- `ASTRO_PROJECT_IMAGE_REF=<image ref>` may record the prepared image source
- `ASTRO_FIXED_AGENT_TYPE` may pin backend identity, commonly `project-executor` for existing Main
  Sequence worker sessions

Runtime behavior:

- runs inside the prepared project cwd
- treats the prepared project cwd as canonical project state
- does not select, create, or set up another project unless explicitly asked
- uses backend/session model authority for the active session
- may use project-local `.agents/skills` when the project provides them

Container shape:

- production image is built with [`Dockerfile.remote-worker`](../../Dockerfile.remote-worker)
- Astro runtime lives under `/app`
- prepared project tree lives at `ASTRO_FIXED_PROJECT_CWD`, normally
  `/usr/local/share/user-skel/app`

## Shared Filesystem And Sidecar Contract

Both runtime contexts should use the same Astro filesystem contract wherever the base image permits
it:

```text
HOME=/home/jovyan
ASTRO_CONTAINER_DATA_DIR=/home/jovyan/.astro-container-data
ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/jovyan/.astro-container-data/.config/mainsequence
PI_CODING_AGENT_DIR=/home/jovyan/.astro-container-data/.pi/agent
ASTRO_SESSION_STATE_DIR=/session-state
ASTRO_STREAM_SESSION_DIR=/session-state/sessions
ASTRO_SESSION_OVERRIDES_DIR=/session-state/session-overrides
ASTRO_PROVIDER_CREDENTIAL_DIR=/session-state/pi-agent-auth
```

The checkpoint sidecar for a runtime must:

- mount the same `/session-state` volume as its matching stream container
- use the same home/config/Pi path contract as its matching stream container
- never point at `/home/appuser` when the stream container uses `/home/jovyan`
- flush checkpoint bundles keyed by backend `AgentSession.uid`
- not infer backend identity from local paths

If the stream container runs with `/home/jovyan` but the sidecar tries to initialize Main Sequence
state under `/home/appuser`, provider credential hydration and checkpoint flushing can fail before
history reaches the backend. That is a deployment bug, not a session-history bug.

## Prompt Contract

Both runtime contexts use the shared package/system prompt contract.

Project-attached behavior is selected by runtime context, not by a separate bundled executor prompt
file. Project-local or session-local skills may extend behavior, but they do not change backend
`agentType`, sidecar paths, A2A envelope rules, or filesystem isolation.
