# Decisions

## 1. Always-on repository documentation context
**Decision:** extension hook, not a subagent.

Implementation:
- `extensions/docs-context/index.ts`
- generated files in `knowledge/`

Why:
- this is cross-cutting context, not a specialist role
- `before_agent_start` is the right place to append it
- the context should be available to both parent and child processes

Alternative:
- use `context` instead of `before_agent_start` if you want every internal LLM call to re-evaluate different context shaping rules

## 2. Main Sequence project coder
**Decision:** project-local specialist.

Implementation:
- `.pi/agents/mainsequence-project-coder.md`
- invoked through `delegate_specialist`

Why:
- it deserves a separate role, separate prompt, and separate context window
- it can implement directly inside the checked-out Main Sequence project folder

Important nuance:
- in Astro, `mainsequence-project-coder` is implementation-first
- the parent session prepares the handoff files and orchestration context, then the child implements in the target project

Alternative:
- split it later into multiple task-specific coding specialists if one implementation role becomes too broad

## 3. Bug / inconsistency checker
**Decision:** specialist plus a helper tool.

Implementation:
- `.pi/agents/doc-bug-auditor.md`
- `audit_recent_changes` helper in `extensions/recent-changes/index.ts`

Why:
- the audit role is specialist-shaped
- the trigger path is tool-shaped for Astro-side edits and specialist-shaped for target-project status review

Alternative:
- add an automatic post-edit review hook later if you want review to run more aggressively

## 4. Web information gatherer
**Decision:** standard external Pi package, not a repo-local extension.

Implementation:
- `.pi/settings.json`
- `npm:pi-web-access`

Why:
- this is a capability, not a role
- the parent agent can decide when it needs fresh external context
- the community package already covers search and content fetching
- avoiding a local duplicate keeps Astro easier to maintain

Alternative:
- add a thin Astro-specific wrapper only if the external package proves insufficient

## 5. Parent-agent vs child-specialist policy split
**Decision:** static parent prompt, runtime child policy.

Implementation:
- `.pi/APPEND_SYSTEM.md`
- `config/project-policy-specialist.md`
- `extensions/project-policy/index.ts`

Why:
- the parent prompt is mostly stable and easier to inspect as a static file
- the child should stay focused and not re-delegate
- the child still needs runtime-only guardrails

## 6. Split static parent behavior from dynamic child behavior
**Decision:** use the simplest mechanism that matches each role.

Implementation:
- `.pi/APPEND_SYSTEM.md` is the parent source of truth
- `delegate_specialist` passes an explicit child append prompt
- `project-policy` only adds child-specialist rules at runtime
- `docs-context` still adds current-turn repo context dynamically

Why:
- the parent flow should be easy to understand without extra runtime indirection
- the child flow still needs a runtime branch
- this is simpler than keeping both parent and child policy switching in an extension

## 7. Keep Astro's repo-local runtime in TypeScript
**Decision:** TypeScript-first local runtime, with external Pi packages when available.

Why:
- Pi extensions are TypeScript entry points
- the current repo-local logic is simple enough that a language bridge adds more complexity than value
- removing the extra local runtime layer makes the system easier to inspect end-to-end

Alternative:
- add a supported external Pi package before inventing a repo-local wrapper
