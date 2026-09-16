# ADR 0004: Minimal Bundled Tool Boundary

Status: Accepted

Date: 2026-09-16

## Context

Migration-era optional tools expanded dependency size and implied product ownership of browsing,
search, video extraction, and runtime-inspection behavior that is unrelated to the SDK's transport
and execution responsibilities.

## Decision

The base durable Tau session contains:

- Tau's core `read`, `write`, `edit`, and `bash` coding tools;
- authenticated Main Sequence MCP tools; and
- protocol-required task interruption controls.

The SDK does not bundle generic web access, web or code search, URL fetching, content extraction,
video handling, structured filesystem duplicates, or a model-facing runtime-information tool. It
does not dynamically install optional tool dependencies.

A project that needs an optional capability adds the implementation and dependencies in its own
environment and registers it through its `.tau` extension. HTTP libraries required for SDK backend
and MCP communication remain runtime dependencies; they are transport implementation, not a
model-facing browsing tool.

## Consequences

- The base package remains focused and its tool surface is inspectable.
- Optional tool risk, maintenance, and dependencies are visibly owned by the consuming project.
- Removing an optional tool does not remove Main Sequence HTTP or MCP communication.
