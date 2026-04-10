# Decisions

## 1. Keep the parent prompt static

Implementation:

- `.pi/APPEND_SYSTEM.md`

Why:

- the parent role is mostly stable
- static prompt files are easier to inspect than runtime-generated parent policy

## 2. Keep child policy dynamic

Implementation:

- `pi/extensions/hooks/project-policy/child-policy.md`
- `pi/extensions/hooks/project-policy/index.ts`

Why:

- child specialists should not behave like the parent
- child-only guardrails are naturally runtime-specific

## 3. Use specialists for focused delegated work

Implementation:

- `.pi/agents/mainsequence-project-coder.md`
- `.pi/agents/rpro-builder.md`

Why:

- implementation and fixed-guideline builds are different roles
- child sessions get narrower context windows and clearer responsibilities

## 4. Keep repo-local runtime in TypeScript

Implementation:

- `pi/extensions/hooks/`
- `pi/extensions/tools/`
- `pi/extensions/shared/`
- `scripts/*.ts`

Why:

- the local runtime logic is mostly orchestration and file shaping
- avoiding a local language bridge keeps Astro easier to debug

## 5. Prefer external packages for generic capabilities

Implementation:

- `.pi/settings.json`
- `npm:pi-web-access`

Why:

- Astro should not duplicate generic web capabilities locally when a maintained package already exists

## 6. Keep tutorial verification as a parent workflow

Implementation:

- `pi/prompts/verify-mainsequence-tutorial.md`

Why:

- it is a fixed CI-shaped workflow
- the build step belongs in a specialist, but the overall verification logic belongs to the parent

## 7. Do not auto-inject docs into agent context

Implementation:

- `docs-context` extension removed

Why:

- the agent already has role instructions in `.pi/APPEND_SYSTEM.md` and `.pi/agents/*.md`
- user-facing docs should not be mixed into the agent prompt by default
