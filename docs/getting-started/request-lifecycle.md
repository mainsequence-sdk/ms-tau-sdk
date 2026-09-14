# Request Lifecycle

## Durable chat

1. The caller creates or selects a backend `AgentSession`.
2. `POST /api/chat` supplies that session UID and the latest user message.
3. Astro authenticates to Django with its runtime credential.
4. The session manager acquires the backend runtime lease.
5. Astro sends the exact backend AgentSession UID to Django; Django derives
   that session's User owner and returns only the requested provider
   credential. The runtime responsible User remains the acting principal.
6. Tau discovers project-owned skills directly from `<repository>/.agents/skills` and, when the
   executor setting is enabled, project extensions from `<repository>/.tau/extensions`.
7. Astro connects to Django `/mcp` with the same runtime bearer token.
8. Main Sequence MCP tools and platform skills are exposed to Tau. For Agent
   list/search, Astro removes the environment selector from the Tau schema and
   injects the backend-provided CodeRepositoryBranch environment at transport time.
9. Astro builds the Tau provider and `CodingSession` in memory.
10. Tau entries are read from and appended directly to Django.
11. Tau events are translated to assistant-ui SSE chunks.
12. The loaded runtime remains warm until idle eviction or shutdown.

There is no child coding-agent process, JSONL session file, checkpoint bundle,
or checkpoint sidecar.

## Agent-targeted sessionless response

`POST /api/agents/{agent_uid}/responses` validates the path UID against the
immutable deployment snapshot, resolves Agent provider/model/thinking defaults,
hydrates only the requested provider credential with `agent_uid`, creates a
short-lived Tau `AgentHarness`, returns a canonical A2A Message, and closes the
provider. It does not call session, task, entry, checkpoint, capability, or
agent lookup endpoints. The streaming variant uses the same execution path and
emits one final Message event.

## Tools and code repository context

Tau core coding tools run against the mounted `/workspace`. Astro adds runtime
information and the Main Sequence MCP tools and resources. File discovery
comes from `tau-file-tools`; web search and extraction come from
`tau-web-access`.

CodeRepository-owned skills are discovered directly from the mounted code repository's
`.agents/skills` directory. CodeRepository-owned executable extensions are discovered from
`.tau/extensions` when enabled by the executor deployment. General Main Sequence platform skills
and operations come from Django `/mcp`; SDK skills are not copied into the code repository. Astro
does not fetch or materialize backend AgentCapability/session-binding overlays.
