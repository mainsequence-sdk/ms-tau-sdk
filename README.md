![Main Sequence logo](https://api.main-sequence.app/static/media/logos/MS_logo_long_black.png)

# Astro

Astro is a Pi package that acts as a parent orchestrator for Main Sequence project assistants.

It translates user intent into a Main Sequence project workflow, prepares `astro/` handoff files in the checked-out project, delegates implementation or review to specialists, and can also run a fixed tutorial-regression workflow for the official Main Sequence docs.

## Quick start

```bash
npm run pi
```

For the tutorial-regression workflow:

```bash
pi
```

To launch only the coding specialist instead of the full orchestrator:

```bash
npm run specialist -- --agent mainsequence-project-coder --cwd /absolute/path/to/checked-out-project
```

## Start reading here

- [`docs/README.md`](./docs/README.md)
  - canonical documentation hub with the architecture diagram and ordered reading paths
- [`docs/getting-started/quickstart.md`](./docs/getting-started/quickstart.md)
  - shortest path to running Astro
- [`docs/getting-started/pi-primer.md`](./docs/getting-started/pi-primer.md)
  - Pi concepts used by Astro, explained for readers who are new to Pi

## Main workflows

- [`docs/workflows/main-sequence-project-flow.md`](./docs/workflows/main-sequence-project-flow.md)
- [`docs/workflows/tutorial-verification.md`](./docs/workflows/tutorial-verification.md)

## Pi components in this repo

- [`docs/components/settings-and-system-prompt.md`](./docs/components/settings-and-system-prompt.md)
- [`docs/components/extensions.md`](./docs/components/extensions.md)
- [`docs/components/agents.md`](./docs/components/agents.md)
- [`docs/components/prompts.md`](./docs/components/prompts.md)
- [`docs/components/skills.md`](./docs/components/skills.md)
- [`docs/components/knowledge.md`](./docs/components/knowledge.md)
- [`docs/components/scripts-and-runtime.md`](./docs/components/scripts-and-runtime.md)

## Generated context

Astro no longer uses a generated `knowledge/` cache. All canonical docs live under `docs/`.
