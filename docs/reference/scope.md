# Scope

Astro is a Python 3.13 service that embeds Tau for Main Sequence project
execution. It owns HTTP/A2A protocol translation, backend session durability,
provider hydration, project policy, backend capability materialization,
automatic Main Sequence MCP integration, and Astro-specific tools.

Tau owns the coding agent loop and its core tools. `tau-file-tools` owns the
independent `grep`, `find`, and `ls` tools. `tau-web-access` owns search,
content retrieval, and document/video extraction. The project owns its local
`.agents` content; Django MCP owns general Main Sequence platform resources and
operations.

Astro does not contain a Node runtime, a Pi compatibility layer, a browser UI,
or a generic retrieval database.
