# Folder structure

```text
astro/
├── .pi/
│   ├── APPEND_SYSTEM.md
│   ├── settings.json
│   └── agents/
│       ├── mainsequence-project-coder.md
│       └── rpro-builder.md
├── pi/
│   ├── extensions/
│   │   ├── hooks/
│   │   ├── tools/
│   │   └── shared/
│   ├── prompts/
│   └── skills/
├── docs/
│   ├── getting-started/
│   ├── components/
│   ├── workflows/
│   ├── reference/
│   └── reserach_guide/
├── interface/
│   └── stream/
├── scripts/
├── AGENTS.md
├── Dockerfile
├── README.md
├── package.json
├── tsconfig.json
└── types/
```

## What each top-level area is for

### `.pi/`

Project-local Pi settings, parent prompt, and specialist prompts.

### `pi/`

Pi package content registered in `package.json`, including extensions, prompts, and skills. Shared helpers live under `pi/extensions/shared/`.

### `docs/`

Canonical human-readable documentation for Astro.

### `interface/`

Optional runtime interfaces around Pi, including the HTTP stream wrapper.

### `scripts/`

Command-line helpers for launching Astro and running dedicated workflows.

### `Dockerfile`

Optional Python 3.11 runtime for `mainsequence` and related Python tasks.
