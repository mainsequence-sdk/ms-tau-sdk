# TypeScript runtime

## Short answer

Astro's repo-local runtime is now TypeScript-only.

The repo also includes a root `Dockerfile` with Python 3.11, `uv`, and `mainsequence`, but that image is infrastructure for Python tasks, not Astro's repo-local runtime.

## The pattern used in this starter

### TypeScript does:
- register hooks
- register tools
- read generated repo context
- orchestrate specialist delegation
- keep shared helper logic in `extensions/shared/`

### External packages do:
- provide broader capabilities Astro should not reimplement locally
- expose tools such as `web_search`, `fetch_content`, and `get_search_content`

## Why this is the current default

- the local runtime logic is small and easy to inspect in TypeScript
- removing the language bridge makes Astro easier to debug end-to-end
- standard community packages are preferred over duplicate repo-local wrappers

## Current shape

- Astro's own runtime lives under `extensions/`
- shared helpers live under `extensions/shared/`
- external runtime capabilities are referenced from `.pi/settings.json`

## When to keep something in TypeScript

Keep it in TypeScript when it is mostly:
- event wiring
- tool registration
- file reads
- path routing
- child-process orchestration
- lightweight formatting or transformation

## When to avoid adding local code

Prefer an external Pi package when:
- the capability already exists in a maintained community package
- Astro would otherwise just wrap a generic integration
- keeping the repo smaller is more valuable than custom control

## Alternative path later

If Astro grows more shared repo-local logic later, add TypeScript helpers under `extensions/shared/` rather than reintroducing another local runtime layer by default.
