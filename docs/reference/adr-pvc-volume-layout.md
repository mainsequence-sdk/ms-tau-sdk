# ADR: PVC-Like Runtime Volume Layout

## Status

Accepted

## Context

Astro's container runtime state was previously split across:

- repo-local `./.astro`
- host-mounted `${HOME}/.pi/agent`
- Docker named volume `astro_container_data`

That layout was convenient for local inspection, but it did not simulate a Kubernetes PVC-backed
deployment well. In particular:

- Pi runtime state did not live under a standard Pi folder inside the durable volume
- Astro stream session state lived outside the container volume
- host-authenticated Pi session state was not being folded into the durable volume cleanly

We want local Docker behavior to look much closer to a GKE deployment that mounts one durable PVC.

## Decision

Astro now treats `astro_container_data` as the canonical durable state root for containerized
runtime state.

Inside that volume, Astro uses:

- `.pi/agent`
  - canonical Pi runtime folder inside the durable volume
- `.config/mainsequence`
  - Main Sequence CLI auth/config
- `.astro/stream-sessions`
  - Astro HTTP stream/session artifacts
- `mainsequence`
- `mainsequence-dev`
- `uv`

Container env now points directly at the volume-backed paths:

- `PI_CODING_AGENT_DIR=/root/.astro-container-data/.pi/agent`
- `ASTRO_MAINSEQUENCE_CONFIG_DIR=/root/.astro-container-data/.config/mainsequence`
- `ASTRO_STREAM_SESSION_DIR=/root/.astro-container-data/.astro/stream-sessions`
- `ASTRO_CONTAINER_DATA_DIR=/root/.astro-container-data`

For compatibility and operator ergonomics, Astro also creates standard home-directory symlinks:

- `/root/.pi/agent -> /root/.astro-container-data/.pi/agent`
- `/root/.config/mainsequence -> /root/.astro-container-data/.config/mainsequence`
- `/root/.astro/stream-sessions -> /root/.astro-container-data/.astro/stream-sessions`
- `/root/mainsequence -> /root/.astro-container-data/mainsequence`
- `/root/mainsequence-dev -> /root/.astro-container-data/mainsequence-dev`
- `/root/.local/share/uv -> /root/.astro-container-data/uv`

## One-Time Migration

Astro performs a one-time migration into the volume when the marker file below is absent:

- `/root/.astro-container-data/.astro/migrations/pvc-layout-v1.json`

Migration sources:

- repo-local legacy Astro state
  - mounted read-only at `ASTRO_LEGACY_REPO_STATE_DIR`
- host Pi agent state
  - mounted read-only at `ASTRO_LEGACY_HOST_PI_AGENT_DIR`

Migration precedence:

1. Copy Astro-owned runtime files from the legacy repo-local `pi-agent-runtime` into `.pi/agent`
2. Copy `mainsequence-config` into `.config/mainsequence`
3. Copy `stream-sessions` into `.astro/stream-sessions`
4. Merge `auth.json` and `sessions/` from the legacy host Pi agent dir into `.pi/agent`
5. Write the migration marker and stop importing legacy state on future boots

This keeps the volume as the long-term source of truth while still preserving existing local state.

## Consequences

### Positive

- local Docker now simulates a single-PVC deployment much more closely
- Pi runtime state lives under a standard `.pi/agent` folder inside the durable volume
- Astro-only state is still clearly separated under `.astro/`
- host Pi auth/session state is merged once instead of being live-linked forever

### Negative

- repo-local `./.astro` is no longer the active runtime state for containers
- developers must inspect the named volume or the symlinked runtime paths inside the container
- one-time migration logic adds bootstrap complexity

## Tasks

- [x] Move container runtime env paths to `astro_container_data`
- [x] Keep standard Pi folder layout inside the durable volume
- [x] Add one-time migration from legacy repo-local Astro state
- [x] Add one-time merge from host Pi auth/session state
- [x] Persist a migration marker to stop repeated imports
