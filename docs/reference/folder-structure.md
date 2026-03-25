# Folder structure

```text
astro/
├── .pi/
│   ├── APPEND_SYSTEM.md
│   ├── settings.json
│   └── agents/
│       ├── mainsequence-project-coder.md
│       ├── rpro-builder.md
│       └── doc-bug-auditor.md
├── config/
│   ├── project-policy-specialist.md
│   └── rpro-builder-guidelines.md
├── docs/
│   ├── getting-started/
│   ├── components/
│   ├── workflows/
│   └── reference/
├── extensions/
│   ├── docs-context/
│   ├── project-policy/
│   ├── specialist-delegate/
│   ├── recent-changes/
│   └── shared/
├── knowledge/
├── prompts/
├── scripts/
├── skills/
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

### `config/`

Human-readable runtime configuration and shared specialist guideline files.

### `docs/`

Canonical human-readable documentation for Astro.

### `extensions/`

Astro's custom Pi hooks, tools, and shared TypeScript helpers.

### `knowledge/`

Generated context files used by the agent.

### `prompts/`

Reusable workflow prompts for the parent agent.

### `scripts/`

Command-line helpers for launching Astro and refreshing generated context.

### `skills/`

Optional deep instruction bundles the agent can load when needed.

### `Dockerfile`

Optional Python 3.11 runtime for `mainsequence` and related Python tasks.

