# Runtime Entrypoints And Tools

Astro separates production runtime code from adapter code and local tooling.

## Top-Level Layout

- `bin/`
  - thin process entrypoints
- `runtime/`
  - Astro Core runtime services and bootstrap code
- `adapters/`
  - backend/platform-specific adapter code
- `tools/`
  - build, debug, and live-test utilities

## Production Entrypoints

### `bin/astro-stream.ts`

Starts the HTTP streaming interface wrapper around Pi.

It runs the Astro runtime bootstrap, logs resolved package/skill state, and then imports
`interface/stream/server.ts`.

### `runtime/checkpoints/sidecar.ts`

Runs the checkpoint sidecar service.

This is production runtime code, not a local helper. It watches the session-state filesystem,
debounces changes, detects checkpoint/compaction markers, and flushes complete checkpoint bundles
to the backend.

### `bin/astro-pi-local.ts`

Starts the local interactive Pi launcher.

This is a development entrypoint. It uses the same Main Sequence runtime-auth adapter as the stream
server, then launches the local `pi` CLI from the prepared orchestrator runtime cwd.

## Runtime Libraries

### `runtime/bootstrap/pi-agent-dir.mjs`

Builds the writable Pi runtime layout:

- agent and project-local `.pi/settings.json`
- configured package paths from `ASTRO_PI_PACKAGE_PATHS`
- package-provided prompt composition
- startup skill resolution from configured Pi packages before SDK fallback
- container-local cleanup for provider auth state

## Adapter Code

### `adapters/mainsequence/runtime-auth.ts`

Owns Main Sequence runtime credential auth for Astro.

Both `bin/astro-stream.ts` through the stream server and `bin/astro-pi-local.ts` use this module.
Do not duplicate Main Sequence CLI auth logic in entrypoints.

### `adapters/mainsequence/bin/cli-auth-repair.ts`

Host-side command used by the Main Sequence Pi package tool to repair runtime-managed CLI auth.

## Local Tools

### `tools/build/patch-pi-rpc-ready.mjs`

Build/vendor patch that makes Pi RPC mode emit the `runtime_ready` sentinel.

### `tools/a2a/debug-endpoint.mjs`

Manual one-shot live A2A debug client.

### `tools/a2a/stress-live.mjs`

Manual live A2A stress client. This is not a unit test; it targets a running Astro endpoint.

## Container Runtime Note

The repo root `Dockerfile` is the deployable app image definition.

The shared `astro-base` stage includes:

- Python 3.11
- Node 20 for Pi
- local npm dependencies
- the runtime folders Astro actually needs:
  - `.pi/`
  - `pi/`
  - `tmp_ms_pi/`
  - `interface/`
  - `runtime/`
  - `adapters/`
  - `bin/`
  - `tools/`

The Dockerfile exposes these runnable targets:

- `astro-pi`
  - starts local Pi through `tsx bin/astro-pi-local.ts`
- `astro-pi-stream`
  - starts the HTTP stream server with `tsx bin/astro-stream.ts`
  - listens on port `8787`
- `astro-session-checkpoint-sidecar`
  - starts the checkpoint sidecar with `tsx runtime/checkpoints/sidecar.ts`

For the explicit deployment identity and sidecar contract, see
[`deployment-identities.md`](./deployment-identities.md).

For the image-backed project executor runtime, see
[`remote-worker-image.md`](./remote-worker-image.md).
