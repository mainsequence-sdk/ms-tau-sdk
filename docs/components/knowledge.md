# Knowledge

`knowledge/` contains generated context files used by Astro.

These files are outputs, not the primary source of truth.

## Generated files

- `knowledge/docs-index.json`
- `knowledge/docs-index.md`
- `knowledge/codebase-map.md`
- `knowledge/agent-context.md`

## Why this exists

Generated knowledge lets Astro load:

- a fast map of the repo
- a summary of important documentation
- a compact list of specialists and extension tools

without rereading every source file manually each time.

## How to refresh it

Use either:

- `refresh_docs_index` from inside Astro
- `npm run docs:index` from the command line

Both paths use the same TypeScript implementation in `extensions/shared/docsIndex.ts`.

## Editorial rule

Human-readable docs live in `docs/`.

Generated summaries live in `knowledge/`.

Do not edit generated files as if they were canonical documentation.

## Related pages

- [`scripts-and-runtime.md`](./scripts-and-runtime.md)
- [`../reference/folder-structure.md`](../reference/folder-structure.md)

