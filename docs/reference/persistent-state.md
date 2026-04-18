# Persistent state policy

Astro containers are disposable. Runtime state that matters must not live only inside container-local
filesystems.

## Policy

- Any state required for hydration, debugging, deterministic tool endpoints, or session continuity
  must live on a durable volume-backed path.
- Disposable container storage is allowed only for temporary files, ephemeral prompts, and caches
  that can be safely rebuilt.
- Containerized Astro services should treat `astro_container_data` as the durable runtime root.

## Durable runtime root

The canonical durable container state root is:

```text
/root/.astro-container-data
```

In local Docker, the named volume `astro_container_data` is mounted there. In Kubernetes, this path
is intended to be backed by a single PVC mount. The local volume layout is therefore meant to
replicate the deployed PVC layout as closely as possible.

Current durable subpaths:

- `/root/.astro-container-data/.ssh`
  - pod/container-owned SSH keys
  - persistent `known_hosts`
  - runtime SSH `config`
- `/root/.astro-container-data/project-checkout-runtime`
  - per-project checkout homes used by Astro's `set-up-locally` wrapper
  - project-scoped `.ssh` keys and `known_hosts` so checkout keys are not reused only by slug
- `/root/.astro-container-data/.pi/agent`
  - canonical Pi runtime state inside the durable volume
  - includes Pi `auth.json`, `sessions/`, runtime `settings.json`, and helper binaries
- `/root/.astro-container-data/.config/mainsequence`
  - Main Sequence CLI auth/config
- `/root/.astro-container-data/.astro/stream-sessions`
  - Astro HTTP stream session metadata
  - hydrated chat history snapshots
  - normalized conversation logs
  - raw runtime session logs
- `/root/.astro-container-data/mainsequence`
- `/root/.astro-container-data/mainsequence-dev`
- `/root/.astro-container-data/uv`

## Deployed/PVC layout

When Astro is deployed with one persistent volume, the durable state layout is expected to look
like this:

```text
/root/.astro-container-data/
├── .ssh/
│   ├── config
│   ├── known_hosts
│   └── <repo-specific keys>
├── project-checkout-runtime/
│   └── project-<id>/
│       └── home/
│           ├── .config/
│           │   └── mainsequence -> /root/.astro-container-data/.config/mainsequence
│           └── .ssh/
│               ├── config
│               ├── known_hosts
│               └── <project-scoped keys>
├── .pi/
│   └── agent/
│       ├── auth.json
│       ├── sessions/
│       ├── settings.json
│       ├── astro-model-provider-auth.json
│       ├── astro-model-provider-signin.json
│       └── bin/
├── .config/
│   └── mainsequence/
│       ├── auth.json
│       ├── config.json
│       └── session_overrides/
├── .astro/
│   ├── migrations/
│   │   └── pvc-layout-v1.json
│   └── stream-sessions/
├── mainsequence/
├── mainsequence-dev/
└── uv/
```

The intent is:

- `.ssh`
  - pod-owned SSH runtime state for project checkout and deploy-key flows
- `project-checkout-runtime`
  - project-scoped checkout HOME directories used to avoid basename-only SSH key collisions
- `.pi/agent`
  - standard Pi runtime folder inside the PVC
- `.config/mainsequence`
  - Main Sequence CLI auth and overrides
- `.astro/stream-sessions`
  - Astro-only stream/session artifacts
- `mainsequence`, `mainsequence-dev`, `uv`
  - other durable container-local working state

## Compatibility paths

Astro also creates standard home-directory links for compatibility:

- `/root/.ssh -> /root/.astro-container-data/.ssh`
- `/root/.pi/agent -> /root/.astro-container-data/.pi/agent`
- `/root/.config/mainsequence -> /root/.astro-container-data/.config/mainsequence`
- `/root/.astro/stream-sessions -> /root/.astro-container-data/.astro/stream-sessions`
- `/root/mainsequence -> /root/.astro-container-data/mainsequence`
- `/root/mainsequence-dev -> /root/.astro-container-data/mainsequence-dev`
- `/root/.local/share/uv -> /root/.astro-container-data/uv`

This means operators and tools can still use the standard-looking paths:

- `/root/.pi/agent`
- `/root/.ssh`
- `/root/.config/mainsequence`
- `/root/.astro/stream-sessions`

while the real durable storage still lives under the PVC root.

## Kubernetes guidance

For Kubernetes deployments, use the same runtime contract as local Docker:

- mount one durable volume at `/root/.astro-container-data`
- do not mount a host `~/.ssh`
- allow the pod to generate and persist its own repo SSH keys under `/root/.astro-container-data/.ssh`
- persist `known_hosts` in that same volume so first-contact trust survives pod restarts
- prefer one PVC per Astro runtime instance instead of sharing one writable `.ssh` state across unrelated replicas

## One-time migration

Legacy state can be imported once from:

- repo-local legacy Astro state mounted at `ASTRO_LEGACY_REPO_STATE_DIR`
- host Pi state mounted at `ASTRO_LEGACY_HOST_PI_AGENT_DIR` for `auth.json` and `sessions/`

Migration stops after Astro writes:

```text
/root/.astro-container-data/.astro/migrations/pvc-layout-v1.json
```

After that marker exists, the volume is the only runtime source of truth.

Repo-local `./.astro` is therefore not the active runtime state for containers anymore. It is only
used as a one-time migration source during local development when the marker is missing.

## Allowed ephemeral state

The following may remain ephemeral:

- temporary prompt files created under the OS temp directory
- short-lived child-process scratch directories
- image-layer `node_modules`
- rebuildable caches that are not required for user-visible continuity or debugging

## Implementation rule

For containerized Astro services:

- mount `astro_container_data` to `/root/.astro-container-data`
- in Kubernetes, mount the PVC at `/root/.astro-container-data`
- set `ASTRO_MAINSEQUENCE_CONFIG_DIR=/root/.astro-container-data/.config/mainsequence`
- set `PI_CODING_AGENT_DIR=/root/.astro-container-data/.pi/agent`
- set `ASTRO_STREAM_SESSION_DIR=/root/.astro-container-data/.astro/stream-sessions`
- mount legacy migration sources read-only only when needed for one-time import

If a future feature introduces new runtime state that users or developers may need to inspect later,
that state should be added under `/root/.astro-container-data` in containers.
