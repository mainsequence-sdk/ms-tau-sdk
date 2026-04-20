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
│       └── mainsequence-project-coder.md
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

Project-local Pi settings, parent prompt, and specialist prompts.

### `.astro/`

Legacy repo-local runtime state and migration input for containerized launches. This is no longer
the active durable runtime location for containers once the PVC-style volume layout is enabled.

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

When Astro runs in containers with the PVC-style storage layout, the active durable state is not
the repo-local `.astro/` tree. It lives under the mounted runtime volume root:

```text
/home/appuser/.astro-container-data/
├── .pi/
│   ├── agent/
│   │   ├── auth.json
│   │   ├── sessions/
│   │   ├── settings.json
│   │   ├── astro-model-provider-auth.json
│   │   ├── astro-model-provider-signin.json
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
├── .astro/
│   ├── migrations/
│   │   └── pvc-layout-v1.json
│   └── stream-sessions/
├── mainsequence/
├── mainsequence-dev/
└── uv/
```

So in deployment terms:

- local Docker named volume `astro_container_data` simulates the PVC
- GKE should mount the real PVC at `/home/appuser/.astro-container-data`
- the volume root is the source of truth for all durable container runtime state
- `astro-orchestrator` runs from the writable `astro-orchestrator-runtime` cwd so Pi lock files
  are never written into `/app/.pi`
