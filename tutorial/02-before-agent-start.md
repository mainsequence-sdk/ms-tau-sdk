# `before_agent_start`

This starter uses `before_agent_start` for two things:

1. docs context
2. child-specialist policy

## Why this hook

It runs after the user prompt is submitted but before the agent loop begins.

That makes it the right place for:
- current-turn repo context
- routing rules
- policy that should apply to the whole prompt

## Why docs context is appended to the system prompt

Earlier scaffolds often inject a message.

That works, but if you do it every prompt you can end up with repeated persistent context in the session.

This starter improves that by appending the docs context to the system prompt for the current turn only.

## Why the parent is static but the child stays dynamic

The parent should orchestrate, but the parent instructions are now mostly stable.

So the parent lives in `.pi/APPEND_SYSTEM.md`.

The child should stay in role.

So `project-policy` only appends:
- `config/project-policy-specialist.md`

based on environment variables set by the delegate runtime.
