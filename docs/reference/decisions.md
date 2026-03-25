# Decisions

## 1. Keep the parent prompt static

Implementation:

- `.pi/APPEND_SYSTEM.md`

Why:

- the parent role is mostly stable
- static prompt files are easier to inspect than runtime-generated parent policy

## 2. Keep child policy dynamic

Implementation:

- `config/project-policy-specialist.md`
- `extensions/project-policy/index.ts`

Why:

- child specialists should not behave like the parent
- child-only guardrails are naturally runtime-specific

## 3. Use specialists for focused delegated work

Implementation:

- `.pi/agents/mainsequence-project-coder.md`
- `.pi/agents/rpro-builder.md`
- `.pi/agents/doc-bug-auditor.md`

Why:

- implementation, fixed-guideline builds, and review are different roles
- child sessions get narrower context windows and clearer responsibilities

## 4. Keep repo-local runtime in TypeScript

Implementation:

- `extensions/`
- `extensions/shared/`
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

## 6. Use generated knowledge instead of repeating repo explanations

Implementation:

- `knowledge/`
- `extensions/docs-context/index.ts`
- `extensions/shared/docsIndex.ts`

Why:

- the parent and child both need fast repo context
- generated summaries are cheaper than stuffing long explanations into every prompt

## 7. Keep tutorial verification as a parent workflow

Implementation:

- `prompts/verify-mainsequence-tutorial.md`
- `scripts/run_tutorial_verifier.ts`

Why:

- it is a fixed CI-shaped workflow
- the build step belongs in a specialist, but the overall verification logic belongs to the parent

