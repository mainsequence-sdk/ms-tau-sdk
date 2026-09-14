# Scope

Astro is a Python 3.13 service that embeds Tau for Main Sequence project
execution. It owns HTTP/A2A protocol translation, backend session durability,
provider hydration, project policy,
automatic Main Sequence MCP integration, and Astro-specific tools.

Tau owns the coding agent loop and its core tools. `tau-file-tools` owns the
independent `grep`, `find`, and `ls` tools. `tau-web-access` owns search,
content retrieval, and document/video extraction. The project owns its local
`.agents/skills` and `.tau/extensions` content; Django MCP owns general Main Sequence platform
resources and operations. Backend `runtime_capabilities` remain protocol version negotiation and
A2A Agent Card capabilities remain protocol metadata; neither is a materialized capability
registry.

Astro does not contain a Node runtime, a Pi compatibility layer, a browser UI,
or a generic retrieval database.
