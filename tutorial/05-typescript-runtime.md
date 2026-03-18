# TypeScript runtime

## Why Astro uses this shape

Pi extensions are TypeScript entry points, and Astro's remaining repo-local logic is simple enough that a second runtime would just add friction.

So this starter now keeps its own runtime logic in TypeScript and relies on external Pi packages when a broader capability already exists.

The repo root `Dockerfile` may still provide Python for external tools such as `mainsequence`. That does not change the fact that Astro's own repo-local runtime stays in TypeScript.

## The shape

### Repo-local runtime
- `extensions/*/index.ts`
- `extensions/shared/*`
- `scripts/refresh_docs_index.ts`

### External capability packages
- `.pi/settings.json`
- currently `npm:pi-web-access`

## Why this is a good default

- easy to inspect
- easy to debug
- no cross-language bridge to keep in sync
- fewer moving parts in the local package

## Docs refresh path

Astro's generated `knowledge/` files come from the same TypeScript implementation in `extensions/shared/docsIndex.ts`.

You can reach it in two ways:
- inside Astro through the `refresh_docs_index` tool
- outside Astro through `npm run docs:index`

That keeps the behavior consistent without a separate maintenance runtime.

## When to add more local TypeScript

Add repo-local TypeScript when a capability is:
- specific to Astro's orchestration model
- mostly file or prompt shaping
- mostly routing or delegation logic
- lightweight enough that another runtime would be overkill

## When not to add local code

Do not add a local wrapper when a maintained Pi package already provides the capability well enough.
