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

Why:

- implementation belongs in a checked-out project specialist instead of the parent session
- tutorial verification can still delegate checked-out project work without introducing a second builder role

## 4. Keep repo-local runtime in TypeScript

Implementation:

- `pi/extensions/hooks/`
- `pi/extensions/tools/`
- `pi/extensions/shared/`
- `scripts/*.ts`

Why:

- the local runtime logic is mostly orchestration and file shaping
- avoiding a local language bridge keeps Astro easier to debug

## 5. Keep dependency installation out of `.pi`

Implementation:

- `.pi/settings.json`
- `package.json`
- `node_modules/pi-web-access`

Why:

- the runtime should stay inspectable and avoid generating extra package state inside `.pi`
- external Pi packages should live in normal npm dependency locations

## 6. Keep tutorial verification as a parent workflow

Implementation:

- `pi/prompts/verify-mainsequence-tutorial.md`

Why:

- it is a fixed CI-shaped workflow
- the overall verification logic belongs to the parent, while checked-out project work can still be delegated to `mainsequence-project-coder`

## 7. Do not auto-inject docs into agent context

Implementation:

- `docs-context` extension removed

Why:

- the agent already has role instructions in `.pi/APPEND_SYSTEM.md` and `.pi/agents/*.md`
- user-facing docs should not be mixed into the agent prompt by default
