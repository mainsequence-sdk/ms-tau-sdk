# Specialist discovery

Source: [`pi/extensions/tools/specialist-delegate/agents.ts`](../../../../pi/extensions/tools/specialist-delegate/agents.ts)

## Purpose

Load specialist definitions from user and project agent directories and turn them into the runtime config used by `delegate_specialist`.

## Discovery model

- user agents come from Pi's user agent directory
- project agents come from the nearest `.pi/agents` directory above the current `cwd`
- `scope` can be `user`, `project`, or `both`
- when scope is `both`, project agents override user agents with the same name

## Parsed fields

From each specialist markdown file, the loader reads:

- `name`
- `description`
- `tools`
- `model`
- `outputFormat`
- `appendPromptFiles`

The prompt body becomes the specialist system prompt, and any appended prompt files are resolved relative to the specialist file.

## Main exports

- `discoverAgents(cwd, scope)`
- `AgentConfig`
- `AgentDiscoveryResult`
- `AgentScope`

## Failure handling

Unreadable files, missing frontmatter, or unreadable appended prompt files are skipped rather than crashing discovery.

## Related files

- [`README.md`](./README.md)
- [`runtime.md`](./runtime.md)
- [`../../../../.pi/agents/mainsequence-project-coder.md`](../../../../.pi/agents/mainsequence-project-coder.md)
