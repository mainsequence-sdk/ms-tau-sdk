# Agents

Astro uses project-local runtime agent prompts defined in `.pi/agents/`.

## What a runtime agent prompt looks like

Each runtime agent prompt is a markdown file with frontmatter and a prompt body.

Example shape:

```md
---
name: mainsequence-project-executor
description: Executes project-scoped work inside a Main Sequence project runtime
tools: read, grep, find, ls, bash, edit, write
---

Runtime agent instructions here.
```

## Frontmatter fields used in Astro

- `name`
- `description`
- `tools`
- `model`

The body becomes the runtime agent's appended system prompt.

## Current runtime agents

### `mainsequence-project-executor`

Use when:

- a Main Sequence project runtime needs concrete implementation work
- the orchestrator has already selected and prepared the project context
- the task should run inside the dedicated project runtime rather than in the user-facing orchestrator session

Important rule:

- it should treat the target project's `AGENTS.md` and `.agents/skills/mainsequence-project/SKILL.md` as canonical when they exist
- it follows the same global Main Sequence CLI failure contract as the parent: failed
  `mainsequence ...` commands are reported with the exact command, working directory when relevant,
  exit code or signal, CLI version or version lookup failure, stderr, stdout, and the concrete
  blocker or next action
- it is the only project implementation agent; Astro no longer routes implementation through `mainsequence-project-coder`
- it is reached only through the dedicated executor runtime flow, not through any removed child-launch surface

## Runtime split

Astro intentionally keeps an orchestrator-runtime split:

- parent orchestrates
- executor implements when that later phase is introduced

That keeps the parent focused on Main Sequence operations and keeps implementation on the dedicated project runtime.

## Related pages

- [`prompts.md`](./prompts.md)
