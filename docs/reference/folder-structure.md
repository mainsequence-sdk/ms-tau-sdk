# Folder structure

```text
astro/
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
│   ├── reference/
│   └── reserach_guide/
├── interface/
│   └── stream/
├── scripts/
├── AGENTS.md
├── Dockerfile
├── README.md
├── package.json
└── tsconfig.json
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

Optional app container base with Python 3.11 and Node 20 for running Pi and related tasks.
