# Deployment identities

Astro has two deployment identities. They use the same public vocabulary as backend
`Agent.agent_type`.

Do not introduce a second public name such as `project_worker` for the `project-executor` runtime.
`ASTRO_EXECUTION_MODE=remote_project_worker` is kept only as legacy topology metadata for existing
worker images.

## Contract matrix

| Runtime identity | Selected by | Working root | Request `agentType` | Sidecar path contract |
| --- | --- | --- | --- | --- |
| `astro-orchestrator` | no fixed agent/project env | writable orchestrator runtime cwd | must be `astro-orchestrator` | shared `/session-state`, `/home/jovyan` data roots |
| `project-executor` | `ASTRO_FIXED_AGENT_TYPE=project-executor` plus `ASTRO_FIXED_PROJECT_CWD` | prepared project cwd | may be omitted only because fixed env supplies it; if present it must be `project-executor` | shared `/session-state`, `/home/jovyan` data roots |

`project-executor` is not a specialist prompt, not a separate prompt file, and not a `project_worker`
agent type. It is the fixed project deployment identity of the Astro stream runtime.

## Identity rules

- Backend/session identity is `Agent.agent_type`.
- Runtime profile names must use the same public values: `astro-orchestrator` and
  `project-executor`.
- Fixed runtimes validate request identity before Pi launch.
- A fixed `project-executor` runtime rejects any explicit request/session `agentType` other than
  `project-executor`.
- `ASTRO_EXECUTION_MODE=remote_project_worker` may still appear in deployment env, but it must not
  be exposed as runtime identity, prompt identity, or backend `agent_type`.

## `astro-orchestrator`

This is the normal user-facing stream runtime.

Selection:

- `ASTRO_FIXED_AGENT_TYPE` is unset.
- `ASTRO_FIXED_PROJECT_CWD` is unset.
- requests must provide `agentType="astro-orchestrator"` unless a fixed runtime supplies it.

Runtime behavior:

- runs platform, project-creation, workspace-analysis, SDK, and A2A orchestration workflows
- uses a writable orchestrator cwd prepared at startup, normally
  `/home/jovyan/.astro-container-data/astro-orchestrator-runtime`
- does not treat the process cwd as a prepared project checkout
- can allocate or communicate with project executors through backend-owned sessions and A2A

Container shape:

- built from the repo root [`Dockerfile`](../../Dockerfile) target `astro-pi-stream`
- paired with the `astro-session-checkpoint-sidecar` target from the same image
- main container and sidecar must share `/session-state`

## `project-executor`

This is the fixed project runtime.

Selection:

- `ASTRO_FIXED_AGENT_TYPE=project-executor`
- `ASTRO_FIXED_PROJECT_CWD=<prepared project path>`
- `ASTRO_EXECUTION_MODE=remote_project_worker` may also be set for existing worker topology
- requests must omit `agentType` or send `agentType="project-executor"`
- mismatched request/session `agentType` is rejected before launch

Runtime behavior:

- runs inside the prepared project cwd
- treats the prepared project cwd as canonical project state
- does not select, create, or set up another project unless explicitly asked
- uses backend/session model authority for the target executor session
- refreshes backend session authority before launch so parent/delegating state cannot accidentally
  override target executor policy

Container shape:

- production image is built with [`Dockerfile.remote-worker`](../../Dockerfile.remote-worker)
- Astro runtime lives under `/app`
- prepared project tree lives at `ASTRO_FIXED_PROJECT_CWD`, normally
  `/usr/local/share/user-skel/app`
- local mounted-project harness uses
  [`Dockerfile.remote-worker.local`](../../Dockerfile.remote-worker.local) and `/workspace/project`

## Shared filesystem and sidecar contract

Both deployment identities should use the same Astro runtime filesystem contract wherever the base
image permits it:

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
- not infer runtime identity from local paths; use backend session metadata and the request/session
  `agentType`

If the stream container runs with `/home/jovyan` but the sidecar tries to initialize Main Sequence
state under `/home/appuser`, provider credential hydration and checkpoint flushing can fail before
history reaches the backend. That is a deployment bug, not a session-history bug.

The only intentional filesystem difference between the two deployment identities is the working
root used for Pi execution:

- `astro-orchestrator`: writable orchestrator runtime cwd
- `project-executor`: fixed prepared project cwd

## Prompt contract

Both deployment identities use the shared `.pi/APPEND_SYSTEM.md` prompt contract.

`project-executor` behavior is selected by the fixed runtime env and project cwd, not by a separate
bundled prompt file.

Project-local or session-local skills may extend behavior, but they do not change backend
`agentType`, runtime identity, sidecar paths, A2A envelope rules, or filesystem isolation.
