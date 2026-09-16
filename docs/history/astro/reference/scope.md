# Scope

Astro is a Python 3.13 service that embeds Tau for Main Sequence project
execution. It owns HTTP/A2A protocol translation, backend session durability,
provider hydration, project policy,
automatic Main Sequence MCP integration, and protocol-required A2A task controls.

Tau owns the coding agent loop and its core `read`, `write`, `edit`, and `bash` tools. The project
owns its local `.agents/skills`, `.tau/extensions`, optional tool dependencies, credentials, and
behavior. Django MCP owns general Main Sequence platform resources and operations. Backend
`runtime_capabilities` remain protocol version negotiation and A2A Agent Card capabilities remain
protocol metadata; neither is a materialized capability registry.

Astro does not contain a Node runtime, a Pi compatibility layer, a browser UI,
or a generic retrieval database.
