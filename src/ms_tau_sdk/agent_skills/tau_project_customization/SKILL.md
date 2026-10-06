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
| `TAU_EXCLUDE_BASE_TOOLS` | Replace Tau's `read`, `write`, `edit`, and `bash` with a skill-scoped `read`. |
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
| `true` | `false` | Skill-scoped `read`, Main Sequence MCP, project extension tools, A2A Task controls. |
| `false` | `true` | Coding tools, project extension tools, A2A Task controls. |
| `true` | `true` | Skill-scoped `read`, project extension tools, and A2A Task controls. |

The A2A Task controls are `task_request_input` and `task_request_authorization`. They remain
available in every mode for the agent's own Task; extensions must not register those names.
Register project tools under `.tau/extensions/` and verify the effective catalog after loading.
With base tools excluded, the skill-scoped `read` keeps `.tau/skills/` available: it opens a
discovered skill's `SKILL.md` and the files in its directory, such as `references/*.md`, and
rejects every other path, including symlinks that resolve outside the skill. Tau lists the skills
in the system prompt because a tool named `read` exists. Extensions must not register `read` in
this mode.
Excluding coding tools does not sandbox extension Python code.

## Invariants extensions must not replace

Extensions may change effective agent capabilities, but they must not replace or bypass:

- Main Sequence runtime or user authentication;
- provider-control evidence and credential hydration;
- caller identity, the turn's requester, and active-session proof;
- persistence ordering, leases, cancellation, or task settlement;
- secret redaction and payload limits; or
- HTTP, SSE, Responses, and A2A wire validation.

When Main Sequence MCP is enabled, use its canonical operations for live platform state. Project
tools do not grant new platform permissions merely because they run inside TAU.

## Acting for the person a turn serves

An Agent that an Organization admin enabled for it can read platform data, and ask other platform
applications, with the access of the person whose request a turn is serving: the requester. The
platform keeps that authority and finds the person in its own records; the runtime only proves
which of its sessions it is working on. Extension tools use two SDK functions and nothing else:

```python
import json

from tau_agent.messages import TextContent
from tau_agent.tools import AgentToolResult

from ms_tau_sdk import current_requester, requester_client

ANALYST_DATA_RELEASE_UID = "..."  # the release of the application to ask


async def revenue_by_region(tool_call_id, arguments, signal=None, on_update=None):
    if current_requester() is None:
        return AgentToolResult(content=[TextContent(text="Ask me from your own conversation.")])
    try:
        answer = await requester_client().call_release(
            ANALYST_DATA_RELEASE_UID,
            "POST",
            "/query",
            json={"question": "revenue by region"},
        )
    except PermissionError:
        return AgentToolResult(content=[TextContent(text="Your access for this request ended.")])
    return AgentToolResult(content=[TextContent(text=json.dumps(answer.json()["rows"]))])
```

- `current_requester()` returns the turn's verified requester, with `uid` and `team_uids`, or
  `None`. It is `None` for Agent callers, the platform's own calls, local mode, and code outside a
  turn. Only it names the requester:
  never take a person's UID from tool arguments, the prompt, history, or a header.
- `requester_client()` returns a client bound to the turn. `await client.request("GET",
  "/api/v1/...")` reads a platform API path for the requester. `await client.call_release(
  release_uid, method, path, ...)` asks another platform application, which answers as the
  requester. Both return an `httpx.Response`.
- Without a requester, `requester_client()` raises a `PermissionError`. So does a call that the
  platform refuses because the requester's access ended (`code` `requester_binding_invalid` or
  `runtime_lease_*`): the turn is over, the access was removed, more than 24 hours passed, or the
  Agent is not enabled. Catch it, say in plain words that the request cannot be served, and never
  fall back to the Agent's own access.

Rules for tools that act for the requester:

- Never handle proofs or tokens. The SDK attaches the session, the lease proof, and the
  credentials itself. A tool never reads, logs, stores, or forwards them, passes a path rather than
  a URL, and never sets `Authorization` or an `X-MainSequence-*` header.
- Requester-bound calls are read-only. The platform refuses writes, sharing, and Secret values;
  do not build tools that try them.
- Return to the model only business results, such as rows, numbers, and names. Never return the
  response object, its headers, a token, a proof, or a raw error body.
- Keep nothing for another turn or another person. The binding ends with the turn, and a task the
  tool leaves running has no access afterwards.
- What a tool reads for a person belongs to that person's conversation. Do not write it to shared
  stores, other Agents, or external systems.

People who use such an Agent are told:

> **This Agent works with your identity, securely.** It reads only what you can already read, only
> to answer your own requests, and for at most 24 hours after you ask. It cannot act as anyone else,
> cannot change, share or delete anything, never sees your secret values, and stops the moment your
> access ends. Your Organization's administrator approved it to work this way.

The limit is plain: while it works on your request, the Agent's code can read what you can read,
which is why only administrators decide which Agents may work this way.

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
