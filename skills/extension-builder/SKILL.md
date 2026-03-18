---
name: extension-builder
description: How to add a new extension, specialist, prompt, or shared TypeScript helper in this starter repository.
---

Use this skill when adding new capabilities to the starter.

## Add a new tool

1. Create `extensions/<tool-name>/index.ts`
2. Register a tool with `pi.registerTool(...)`
3. Put reusable helper logic in `extensions/shared/` if the tool needs shared TypeScript utilities
4. Update `docs/` and `tutorial/`
5. Run `refresh_docs_index` or `npm run docs:index`

## Add a new specialist

1. Create `.pi/agents/<name>.md`
2. Add frontmatter:
   - `name`
   - `description`
   - `tools`
   - optional `model`
3. Update `.pi/APPEND_SYSTEM.md` if the parent agent should route to it
4. Update tutorial docs
5. Run `refresh_docs_index` or `npm run docs:index`

## Add a reusable workflow

Use `prompts/` when the main agent should follow a repeatable multi-step pattern but you do not need a new runtime hook.
