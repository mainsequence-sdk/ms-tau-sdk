# Folder structure

```text
astro/
├── .astro/
│   ├── mainsequence-config/
│   ├── pi-agent-runtime/
│   └── stream-sessions/
├── .pi/
│   ├── APPEND_SYSTEM.md
│   ├── settings.json
│   └── agents/
│       └── mainsequence-project-executor.md
├── pi/
│   ├── extensions/
│   │   ├── hooks/
│   │   ├── tools/
│   │   └── shared/
│   ├── prompts/
│   ├── skills/
│   └── types/
├── docs/
│   ├── getting-started/
│   ├── components/
│   ├── extensions/
│   ├── prompts/
│   ├── reference/
│   └── reserach_guide/
├── interface/
│   └── stream/
├── scripts/
├── Dockerfile
├── README.md
├── package.json
└── tsconfig.json
```

## What each top-level area is for

### `.pi/`

Project-local Pi settings, parent prompt, and runtime agent prompts.

### `.astro/`

Ignored repo-local scratch. It is not mounted into containers and is not an active session or
provider-auth source.

### `pi/`

Pi package content registered in `package.json`, including extensions, prompts, and skills. Shared helpers live under `pi/extensions/shared/`.

### `docs/`

Canonical human-readable documentation for Astro, including mirrored per-file docs for
`pi/extensions/` and `pi/prompts/`.

### `interface/`

Optional runtime interfaces around Pi, including the HTTP stream wrapper.

### `scripts/`

Command-line helpers for launching Astro and running dedicated workflows.

### `Dockerfile`

Optional app container base with Python 3.11 and Node 20 for running Pi and related tasks.

## Deployed container layout

When Astro runs in containers, rebuildable runtime state is not the repo-local `.astro/` tree. It
lives under the container runtime root, while active session files live under `/session-state`:

```text
/home/appuser/.astro-container-data/
├── .pi/
│   ├── agent/
│   │   ├── settings.json
│   │   └── bin/
│   └── project/
│       ├── APPEND_SYSTEM.md
│       ├── agents/
│       ├── settings.json
│       └── skills/
├── astro-orchestrator-runtime/
│   └── .pi -> /home/appuser/.astro-container-data/.pi/project
├── .config/
│   └── mainsequence/
│       ├── auth.json
│       ├── config.json
│       └── session_overrides/
├── mainsequence/
├── mainsequence-dev/
└── uv/
```

```text
/session-state/
├── sessions/
├── session-overrides/
├── pi-agent-auth/
├── manifests/
└── checkpoints/
```

So in deployment terms:

- local Docker uses a shared tmpfs-backed `/session-state` volume for active session files
- GKE should mount one shared `emptyDir` at `/session-state` into Astro and the checkpoint sidecar
- backend checkpoints are the durable source of truth for session continuity
- provider auth files live only in scoped `/session-state/pi-agent-auth` directories and are
  hydrated from backend-owned credential storage
- provider auth/signin files in `.pi/agent` are pruned during container startup
- `astro-orchestrator` runs from the writable `astro-orchestrator-runtime` cwd so Pi lock files
  are never written into `/app/.pi`
