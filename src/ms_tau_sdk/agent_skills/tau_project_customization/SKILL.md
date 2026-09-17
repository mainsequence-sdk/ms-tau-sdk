---
name: tau-project-customization
description: Customize project-owned TAU instructions, skills, prompts, hooks, and extension tools while preserving SDK-owned transport and protocol invariants.
---

# TAU Project Customization

Use this skill when changing the effective agent behavior of a repository that runs
`ms-tau-sdk`. The project already executes arbitrary code in the runtime process, so the project
owns and is responsible for its TAU resources and extensions.

## One configuration boundary

The workspace `.tau/` directory is the project behavior boundary:

```text
.tau/
├── SYSTEM.md
├── prompts/
├── skills/
└── extensions/
```

Creating `.tau/SYSTEM.md` replaces the packaged default general system instructions. There is no
second SDK-specific prompt override or companion append file. Other resources follow the TAU
version pinned by the installed SDK.

`.agents/skills/ms_tau_sdk/` is different: it contains managed development instructions for coding
agents working on the repository. It is not loaded as the runtime's `.tau` behavior and must not be
used as application configuration.

## Extension ownership

Project extensions belong under `.tau/extensions/` and may add tools, hooks, and lifecycle
behavior. Keep reusable business logic in the normal application package and make extension
adapters thin and structured.

The project author owns:

- extension source, behavior, and tests;
- Python and operating-system dependencies;
- outbound network access and third-party credentials;
- compatibility with the pinned SDK and TAU versions; and
- reload, cancellation, and shutdown behavior.

The SDK does not sandbox these extensions. Web, fetch, search, browser, video, and other optional
tools are project choices and are not part of the base SDK.

## Invariants extensions must not replace

Extensions may change effective agent capabilities, but they must not replace or bypass:

- Main Sequence runtime or user authentication;
- provider-control evidence and credential hydration;
- caller identity and active-session proof;
- persistence ordering, leases, cancellation, or task settlement;
- secret redaction and payload limits; or
- HTTP, SSE, Responses, and A2A wire validation.

Use canonical Main Sequence MCP operations for live platform state. Project tools do not grant new
platform permissions merely because they run inside TAU.

## Validation

Verify each project-owned capability through its observable interface and focused tests. Check
that documented tool names and schemas match the loaded catalog, lifecycle hooks clean up their
resources, and health diagnostics report extension load errors without exposing prompt content or
secrets.
