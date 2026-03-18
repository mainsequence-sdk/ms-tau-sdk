# Agent files and frontmatter

Each specialist is a markdown file.

Example shape:

```md
---
name: mainsequence-project-coder
description: Implements tasks inside a checked-out Main Sequence project
tools: read, grep, find, ls, bash, edit, write
model: claude-sonnet-4-20250514
---

Specialist instructions here.
```

## Fields used in this starter

- `name`
- `description`
- `tools`
- `model`

## What the body becomes

The body becomes the specialist's appended system prompt.

## Good practice

Make specialist prompts:
- role-specific
- concrete
- structured in output
- small enough to maintain
