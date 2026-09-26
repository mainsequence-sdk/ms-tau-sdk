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

## Runtime tool composition

Two independent process settings select the host-provided tools. Both default to `false`:

| Setting | Effect when `true` |
| --- | --- |
| `TAU_EXCLUDE_BASE_TOOLS` | Omit Tau's `read`, `write`, `edit`, and `bash`. |
| `TAU_EXCLUDE_MAINSEQUENCE_MCP` | Skip Main Sequence MCP connection, tools, resources, and resource prompt. |

Set them in `harness_agent.spec.env_vars` in a managed repository workflow, in the process
environment for local `ms-tau`, or through explicit `TauSDKSettings` fields in a Python host. They
apply to all sessions in that process; neither Agent Card skills nor prompt text removes tools.

The accepted, pending job-hosted batch entry point uses these same two settings. It must not
start Main Sequence MCP or add coding tools when the corresponding source is excluded. The table
below describes the currently implemented service and local modes; batch behavior for A2A-only
Task controls requires an actual Task context and is specified by ADR 0015.

| Exclude base tools | Exclude Main Sequence MCP | Model-facing sources |
| --- | --- | --- |
| `false` | `false` | Coding tools, Main Sequence MCP, project extension tools, A2A Task controls. |
| `true` | `false` | Main Sequence MCP, project extension tools, A2A Task controls. |
| `false` | `true` | Coding tools, project extension tools, A2A Task controls. |
| `true` | `true` | Project extension tools and A2A Task controls. |

The A2A Task controls are `task_request_input` and `task_request_authorization`. They remain
available in every mode for the agent's own Task; extensions must not register those names.
Register project tools under `.tau/extensions/` and verify the effective catalog after loading.
With `read` excluded, Tau 0.4.2 does not insert discovered skills into the system prompt. Put
needed guidance in the effective `.tau/SYSTEM.md` or expose it through a declared retrieval tool.
Excluding coding tools does not sandbox extension Python code.

## Invariants extensions must not replace

Extensions may change effective agent capabilities, but they must not replace or bypass:

- Main Sequence runtime or user authentication;
- provider-control evidence and credential hydration;
- caller identity and active-session proof;
- persistence ordering, leases, cancellation, or task settlement;
- secret redaction and payload limits; or
- HTTP, SSE, Responses, and A2A wire validation.

When Main Sequence MCP is enabled, use its canonical operations for live platform state. Project
tools do not grant new platform permissions merely because they run inside TAU.

## Validation

Verify each project-owned capability through its observable interface and focused tests. Check
that documented tool names and schemas match the loaded catalog, lifecycle hooks clean up their
resources, and health diagnostics report extension load errors without exposing prompt content or
secrets.

For interactive local verification, start Tau in local mode, load a session through Chat or A2A,
then open Tau Board's Agent tab. Inspect the effective tool category, JSON Schema, extension entry
source, and diagnostics. For a project-owned tool, fill the generated form or raw JSON, validate
the canonical arguments, explicitly acknowledge the side-effect warning, and run it. This invokes
the exact loaded tool without a model turn or conversation/Task-history entry. It is not a sandbox:
filesystem, network, credential, and external-system side effects are the project's responsibility.
