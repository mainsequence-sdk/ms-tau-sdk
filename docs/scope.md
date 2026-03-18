# Scope

## In scope for this starter

- Main Sequence project orchestration flow
- actual specialist delegation wiring
- generated documentation context
- static parent prompt plus child-specialist policy injection at `before_agent_start`
- project-local specialist discovery from `.pi/agents/`
- child `pi` spawning for specialists, including checked-out project folders
- project handoff files under `astro/`
- recent-change tracking in the Astro parent session
- status review guidance
- standard external web access through `pi-web-access`
- TypeScript-only repo-local runtime
- tutorial material explaining alternatives and extension paths

## Explicitly out of scope for this first iteration

- patching Pi core
- vector database / full RAG stack
- hidden multi-agent execution on every internal turn
- automatic parallel specialist orchestration
- CI, release automation, packaging to npm, or publishing
- auth / secret management beyond environment variables
- browser automation or deep crawling
- full end-to-end test harness

## Why these are out of scope

The purpose of this repository is to give you a **clear, teachable Main Sequence orchestration starting point**.

That means:
- real wiring, yes
- maximum complexity, no

You can add the larger pieces once the extension boundaries feel natural.
