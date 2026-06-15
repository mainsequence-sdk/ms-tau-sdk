# Stateless runtime storage policy

Astro containers are disposable. Active session files live on pod-local storage, and durable
continuity comes from backend checkpoints as described in
[`adr-emptydir-session-checkpoint-storage.md`](./adr-emptydir-session-checkpoint-storage.md).

## Policy

- Any state required for session continuity must be restored from and flushed to backend checkpoints.
- Disposable container storage is allowed for active session files, temporary files, ephemeral
  prompts, and caches that can be safely rebuilt.
- Containerized Astro services must treat `/home/jovyan/.astro-container-data` as rebuildable
  runtime state, not a PVC or durable user/session store.
- Provider auth, provider signin state, and stream session files must have no host or repo-local
  source path in the container.
- Containerized Astro startup prunes Pi provider auth/signin state from `PI_CODING_AGENT_DIR`
  (`auth.json`, `sessions/`, `astro-model-provider-auth.json`, and
  `astro-model-provider-signin.json`) before the runtime starts.
- Containerized Astro startup also prunes `/session-state/pi-agent-auth` so a restarted container
  cannot reuse scoped provider credentials from an earlier process.

## Container runtime root

The canonical container runtime root is:

```text
/home/jovyan/.astro-container-data
```

In local Docker, this path is container-local. Active session files live under
`/session-state/sessions`, backed by tmpfs in Compose and by `emptyDir` in Kubernetes. The image runs
as non-root `jovyan`.

Current runtime subpaths:

- `/home/jovyan/.astro-container-data/.ssh`
  - pod/container-owned SSH keys
  - rebuildable `known_hosts`
  - runtime SSH `config`
- `/home/jovyan/.astro-container-data/.pi/agent`
  - container-local Pi runtime state
  - includes runtime `settings.json` and helper binaries
  - provider `auth.json` and Pi `sessions/` are pruned at startup and must not be used as durable
    state
- `/home/jovyan/.astro-container-data/.pi/project`
  - writable materialized copy of Astro's repo-local `/app/.pi` project settings
  - used by `astro-orchestrator` so Pi project settings lock files are never written under `/app`
  - follows Pi's standard project settings contract: project settings are loaded from `<cwd>/.pi/settings.json`
- `/home/jovyan/.astro-container-data/astro-orchestrator-runtime`
  - writable cwd for `astro-orchestrator` Pi processes
  - contains `.pi -> /home/jovyan/.astro-container-data/.pi/project`
- `/home/jovyan/.astro-container-data/.config/mainsequence`
  - Main Sequence CLI auth/config
- `/session-state/sessions`
  - Astro HTTP stream session metadata
  - temporary hydrated chat history cache
  - temporary normalized conversation logs
  - raw runtime session logs
- `/session-state/session-overrides`
  - per-session Pi settings overlays that must be visible to both Astro and the checkpoint sidecar
- `/session-state/pi-agent-auth`
  - scoped pod-local Pi auth directories hydrated from backend-owned user provider credentials
  - each scoped directory is removed after sign-in sync or provider-backed stream completion
- `/session-state/manifests`
  - restored checkpoint version, bundle hash, and active lease data for local files
- `/session-state/checkpoints`
  - explicit checkpoint marker files written by Astro at stream boundaries
- `/home/jovyan/.astro-container-data/mainsequence`
- `/home/jovyan/.astro-container-data/mainsequence-dev`
- `/home/jovyan/.astro-container-data/uv`

## Kubernetes guidance

For Kubernetes deployments, use the same session runtime contract as local Docker:

- mount an `emptyDir` at `/session-state`
- mount the same `/session-state` `emptyDir` into both `astro-pi-stream` and the checkpoint sidecar
- run the matching stream container and checkpoint sidecar with the same Astro home/data path
  contract, especially `HOME=/home/jovyan` and
  `ASTRO_CONTAINER_DATA_DIR=/home/jovyan/.astro-container-data`
- do not run a `project-executor` stream container with `/home/jovyan` while its sidecar uses
  `/home/appuser`; that splits Main Sequence config/log initialization and can break credential
  hydration or checkpoint flushes
- expose `POD_UID` to the Astro container; Astro derives the checkpoint holder as `pod/<POD_UID>`
  when `ASTRO_CHECKPOINT_HOLDER_ID` is not explicitly set
- do not mount a host `~/.ssh`
- allow the pod to generate rebuildable repo SSH keys under `/home/jovyan/.astro-container-data/.ssh`
- keep `known_hosts` writable inside the pod so first-contact trust can be recorded
- do not share one writable session filesystem across unrelated replicas

## Allowed ephemeral state

The following may remain ephemeral:

- temporary prompt files created under the OS temp directory
- short-lived child-process scratch directories
- image-layer `node_modules`
- rebuildable caches that are not required for user-visible continuity or debugging

## Implementation rule

For containerized Astro services:

- set `HOME=/home/jovyan`
- set `ASTRO_CONTAINER_DATA_DIR=/home/jovyan/.astro-container-data`
- set `ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/jovyan/.astro-container-data/.config/mainsequence`
- set `PI_CODING_AGENT_DIR=/home/jovyan/.astro-container-data/.pi/agent`
- set `ASTRO_SESSION_STATE_DIR=/session-state`
- set `ASTRO_STREAM_SESSION_DIR=/session-state/sessions`
- set `ASTRO_SESSION_OVERRIDES_DIR=/session-state/session-overrides`
- set `ASTRO_PROVIDER_CREDENTIAL_DIR=/session-state/pi-agent-auth`
- in Kubernetes, mount an `emptyDir` at `/session-state`
- in sidecars, use the same env values and volume mounts as the matching stream container
- let Astro materialize `/app/.pi` into `/home/jovyan/.astro-container-data/.pi/project`
- run `astro-orchestrator` from `/home/jovyan/.astro-container-data/astro-orchestrator-runtime`
- do not mount repo-local or host auth/session directories into the runtime container

If a future feature introduces new runtime scratch, it may live under
`/home/jovyan/.astro-container-data` in containers, but anything required for continuity must move
through backend-owned storage.
