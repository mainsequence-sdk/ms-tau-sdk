# Codebase map

```text
astro/
├── .pi/                      Project-local Pi settings and specialist definitions
├── config/                   Child-specialist policy and other runtime config
├── docs/                     Project documentation
├── tutorial/                 Teaching-oriented extension guide
├── extensions/               Pi hooks and tools
│   ├── docs-context/
│   ├── project-policy/
│   ├── recent-changes/
│   ├── specialist-delegate/
├── knowledge/                Generated repo context used by the agent
├── prompts/                  Reusable prompt templates
├── scripts/                  Utility scripts
├── skills/                   Optional deep instructions
└── Dockerfile                Optional Python 3.11 runtime for mainsequence
```

## External Pi packages
- `pi-web-access` — tools: fetch_content, get_search_content, web_search

## Runtime flow

1. `.pi/APPEND_SYSTEM.md` provides the static parent orchestrator prompt.
2. `docs-context` appends generated repo context at `before_agent_start`.
3. `project-policy` appends child-specialist policy only when Astro spawns a child process.
4. Parent agent uses the Main Sequence CLI, prepares `astro/` files, and may call `delegate_specialist` with a target `cwd`.
5. Child `pi` process runs a specialist from `.pi/agents/` inside the checked-out project folder.
6. Parent agent reviews status directly or via `doc-bug-auditor`; `audit_recent_changes` remains available for Astro-side edits.
