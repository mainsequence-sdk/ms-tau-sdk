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

An Agent that an Organization admin enabled for it can call the platform and other platform
applications with the ordinary permissions of the person whose request a turn is serving: the
requester. The platform keeps that authority and finds the person in its own records; the runtime
only proves which of its sessions it is working on. Extension tools use two SDK functions and
nothing else:

```python
import json

from tau_agent.messages import TextContent
from tau_agent.tools import AgentToolResult

from ms_tau_sdk import platform_client

ANALYST_DATA_RELEASE_UID = "..."  # the release of the application to ask


async def revenue_by_region(tool_call_id, arguments, signal=None, on_update=None):
    # This tool answers each person with their own data, so it needs a person.
    try:
        answer = await platform_client(delegation="required").call_release(
            ANALYST_DATA_RELEASE_UID,
            "POST",
            "/query",
            json={"question": "revenue by region"},
        )
    except PermissionError:
        return AgentToolResult(content=[TextContent(text="Ask me from your own conversation.")])
    return AgentToolResult(content=[TextContent(text=json.dumps(answer.json()["rows"]))])
```

- `current_requester()` returns the person the turn serves, with `uid` and `team_uids`, or
  `None`. The platform names that person when the turn starts, whoever called; it is `None` when
  the platform names nobody, in local mode, and in code outside a turn. Only it names the
  requester: never take a person's UID from tool arguments, the prompt, history, or a header.
- `platform_client()` returns a client. `await client.request("GET", "/api/v1/...")` calls a
  platform API path, and `await client.call_release(release_uid, method, path, ...)` calls another
  platform application. Both return an `httpx.Response`.
- By default (`delegation="auto"`) a call carries the person's delegation while the turn serves a
  person, and is the Agent's own otherwise; the operation or application decides what it may do.
  `delegation="none"` never carries it. `delegation="required"` raises a `PermissionError` before
  sending when the turn serves nobody; use it in a tool that only makes sense for a person.
- A call the platform refuses because the person's access ended (`code`
  `requester_binding_invalid` or `runtime_lease_*`) raises a `PermissionError`: the turn is over,
  the access was removed, more than 24 hours passed, or the Agent is not enabled. Catch it, say in
  plain words that the request cannot be served, and never retry it as the Agent.

Rules for tools that act for the requester:

- Never handle proofs or tokens. The SDK attaches the session, the lease proof, and the
  credentials itself. A tool never reads, logs, stores, or forwards them, passes a path rather than
  a URL, and never sets `Authorization` or an `X-MainSequence-*` header.
- A delegated call uses the person's ordinary permissions, including administrative ones; any other
  call uses the Agent's own. Each operation decides what it needs. Never infer write permission
  from view access.
- This needs the platform's matching change, deployed in the same cutover; check the deployed
  platform and the installed SDK before depending on it.
- Return to the model only business results, such as rows, numbers, and names. Never return the
  response object, its headers, a token, a proof, or a raw error body.
- Keep nothing for another turn or another person. The delegation ends with the turn, and a task
  the tool leaves running acts only as the Agent afterwards.
- What a tool reads for a person belongs to that person's conversation. Do not write it to shared
  stores, other Agents, or external systems.

People who use such an Agent are told:

> **This Agent works with your identity.** It can read, create, change, run,
> share or delete only what your permissions allow through supported operations,
> only while serving your request, and for at most 24 hours after you ask. It
> uses your ordinary permissions, including administrative permissions, and
> access is checked on every call. Your Organization's administrator approved it
> to work this way.

The statement describes delegated calls. The Agent's own grants and trusted extension code remain
separate authority. Prompt injection can cause unintended changes, sharing, or deletion within the
person's permissions, and completed writes can outlast the delegation. Only Organization admins
enable this authority.

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
