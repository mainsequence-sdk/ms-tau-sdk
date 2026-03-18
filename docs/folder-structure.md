# Folder structure

```text
astro/
├── .pi/
│   ├── APPEND_SYSTEM.md
│   ├── settings.json
│   └── agents/
│       ├── mainsequence-project-coder.md
│       └── doc-bug-auditor.md
├── config/
│   └── project-policy-specialist.md
├── docs/
├── tutorial/
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
│   ├── repo-docs/
│   └── extension-builder/
├── AGENTS.md
├── Dockerfile
├── README.md
├── package.json
├── tsconfig.json
└── types/
    └── pi-stubs.d.ts
```

## What each top-level area is for

### `.pi/`
Project-local Pi behavior and specialist prompts. `.pi/APPEND_SYSTEM.md` is the static parent prompt. `.pi/settings.json` also references external Pi packages such as `npm:pi-web-access`, whose local cache lives under `.pi/npm/` and should stay ignored by Git.

### `config/`
Human-readable runtime config, including the child-specialist policy that the extension appends only for child processes.

### `docs/`
Project documentation for the architecture itself.

### `tutorial/`
Teaching content explaining not only what the project does, but how and why it was built this way.

### `extensions/`
Actual Pi hooks, custom tools, and shared TypeScript runtime helpers.

### `knowledge/`
Generated files that the agent can use as fast repo context.

### `prompts/`
Reusable workflow prompts.

### `scripts/`
Maintenance utilities such as regenerating `knowledge/`.

### `skills/`
Optional deep instructions the agent can load when needed.

### `Dockerfile`
Optional Python 3.11 runtime for agent-side Python work such as `mainsequence`.


### `types/`
Tiny local ambient declarations so `npm run check` can do a basic syntax pass even before the real Pi packages are installed.
