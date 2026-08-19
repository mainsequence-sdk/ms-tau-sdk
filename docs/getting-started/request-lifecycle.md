# Request Lifecycle

## Durable chat

1. The caller creates or selects a backend `AgentSession`.
2. `POST /api/chat` supplies that session UID and the latest user message.
3. Astro authenticates to Django with its runtime credential.
4. The session manager acquires the backend runtime lease.
5. Astro hydrates the user-scoped provider credential from Django.
6. Enabled backend session skills are materialized into a private Tau resource root.
7. Astro connects to Django `/mcp` with the same runtime bearer token.
8. Main Sequence MCP tools and resources are exposed to Tau. For Agent
   list/search, Astro removes the environment selector from the Tau schema and
   injects the backend-provided ProjectBranch environment at transport time.
9. Astro builds the Tau provider and `CodingSession` in memory.
10. Tau entries are read from and appended directly to Django.
11. Tau events are translated to assistant-ui SSE chunks.
12. The loaded runtime remains warm until idle eviction or shutdown.

There is no child coding-agent process, JSONL session file, checkpoint bundle,
or checkpoint sidecar.

## Stateless chat

`POST /api/llm/chat` hydrates a provider credential, creates a one-turn Tau
`AgentHarness`, returns JSON, and closes the provider. It does not acquire a
session lease or persist entries.

## Tools and project context

Tau core coding tools run against the mounted `/workspace`. Astro adds runtime
information and the Main Sequence MCP tools and resources. File discovery
comes from `tau-file-tools`; web search and extraction come from
`tau-web-access`.

Project-owned skills are discovered directly from the mounted project's
`.agents/skills` directory. Backend-bound session capabilities continue to be
materialized into the private session resource root. General Main Sequence
platform skills and operations come from Django `/mcp`; SDK skills are not
copied into the project.
