# Project Scope

Main Sequence TAU SDK packages the Python primitives required to run Tau as a Main Sequence coding
agent inside a project workspace.

The SDK owns application construction and lifecycle, authenticated Main Sequence clients, provider
evidence validation, durable and local Tau execution, persistence, transports, protocol tools,
resource defaults, diagnostics, and shutdown.

Tau owns the agent loop, native project resource resolution, extension lifecycle, provider
interfaces, and optional core coding tools. The consuming project owns its source, Python
environment, dependency lock, `.tau` resources and extensions, optional tools, and deployable
artifact.

External Main Sequence services own their APIs, session/task records, credential issuance, and MCP
operations. This repository consumes those contracts but does not define changes to external
repositories, schemas, or deployment systems.
