# ADR 0011: Independent Base-Tool and Main Sequence MCP Exclusion

Status: Accepted; implemented

Date: 2026-09-24

Amended: 2026-10-05 — with base tools excluded, the SDK registers a `read` tool limited to the
files of discovered Tau skills, so project skills stay available
([issue #54](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/54)).

Amends [ADR 0003](./0003-tau-native-project-configuration.md) for host-owned tool
composition settings, [ADR 0004](./0004-minimal-bundled-tool-boundary.md) for the
default tool catalog, and [ADR 0002](./0002-runtime-and-protocol-contracts.md) and
[ADR 0005](./0005-authenticated-local-development-mode.md) for optional MCP projection.

## Context

Before this change, the SDK gave every Tau session four coding tools (`read`, `write`, `edit`,
and `bash`), every tool and resource advertised by its Main Sequence MCP connection,
and two tools needed to interrupt the agent's own A2A Task. Tau then composes
project-registered extension tools with that list. A project can change its
instructions and add tools, but it could not deploy an agent whose domain tools came
only from its own `.tau/extensions` registrations.

The coding tools and Main Sequence MCP are independent runtime capabilities. A
project may need either, both, or neither. The A2A Task controls are a separate
protocol capability: they must remain available in every composition so the agent
can request input or authorization while handling its own Task.

## Decision

### Independent process settings

Add two boolean fields to `TauSDKSettings`, each defaulting to `false`:

| Python setting | Environment variable | Effect when `true` |
| --- | --- | --- |
| `exclude_base_tools` | `TAU_EXCLUDE_BASE_TOOLS` | Do not create Tau's `read`, `write`, `edit`, or `bash` tools; register the skill-scoped `read` instead. |
| `exclude_mainsequence_mcp` | `TAU_EXCLUDE_MAINSEQUENCE_MCP` | Do not connect to Main Sequence MCP or inject its tools, resources, or resource prompt. |

The settings are fixed when the application process starts. They apply to every
session in that process, in managed and local mode. A request, Agent Card, session,
or model response cannot change them. Python applications may pass an explicit
`TauSDKSettings` instance to `create_app`; the `ms-tau` command reads the same
environment settings. These two `TAU_` names are exceptions to the earlier
`MAINSEQUENCE_TAU_*` naming convention because the platform reserves the
`MAINSEQUENCE_` prefix in repository workflow environment variables.

The resulting model-facing catalog is:

| Exclude base tools | Exclude Main Sequence MCP | Effective sources |
| --- | --- | --- |
| No | No | Four coding tools, Main Sequence MCP, project tools, and A2A Task controls. |
| Yes | No | Skill-scoped `read`, Main Sequence MCP, project tools, and A2A Task controls. |
| No | Yes | Four coding tools, project tools, and A2A Task controls. |
| Yes | Yes | Skill-scoped `read`, project tools, and A2A Task controls only. |

`task_request_input` and `task_request_authorization` are unconditional protocol
tools. Neither setting excludes them. A project extension must not replace them:
session loading or extension reload fails if a project registers either reserved
name. This explicit check is necessary because Tau otherwise permits an extension
tool to override an existing tool of the same name. With base tools excluded, `read`
is reserved the same way, so an extension cannot silently put a general file reader
in place of the skill-scoped one.

Project tool registration remains Tau-native through `.tau/extensions`. Both
settings leave project resource and extension discovery enabled; they do not add
an SDK tool manifest or turn Agent Card skill descriptions into executable tool
declarations. The effective catalog must be checked after Tau composes tools on
session load and after extension reload. In the two-exclusions case, every tool
other than the skill-scoped `read` and the two Task controls must come from a
project extension. Unexpected
SDK tools or extension load failures must not silently produce a different
catalog. Safe diagnostics report the selected settings, tool source counts, and
catalog digest without exposing credentials or prompt content.

When MCP is excluded, the runtime does not create a `MainSequenceMCPClient` at
startup or session load. It does not fetch an MCP catalog, offer MCP tools, or
append MCP resource guidance. Backend HTTP authentication, provider hydration,
session persistence, and incoming A2A handling continue. Outbound A2A through
Main Sequence MCP is unavailable in this composition. The existing
`MAINSEQUENCE_TAU_STARTUP_DEPENDENCIES_ENABLED` setting is not used as a substitute:
it controls a wider startup behavior and does not prevent session-load MCP access.

The effective system prompt must not claim that excluded tools or MCP resources
are available. Project `.tau/SYSTEM.md` retains its normal precedence over the
packaged default.

### Skill files with base tools excluded

Tau 0.4.2 lists discovered skills in `<available_skills>` only when the composed
catalog contains a tool named `read`. It checks the name, with the packaged system
prompt and with a project `.tau/SYSTEM.md` alike. As first implemented, excluding
base tools therefore also removed every project skill, and this ADR told projects to
move that guidance into their system prompt or a retrieval tool. The agents that
exclude coding tools for safety, such as agents that handle untrusted content, lost
their skills. Keeping Tau's ordinary `read` is not a fix: it accepts absolute paths
and opens any file the process can, including the process environment that holds the
runtime credential and tokens.

When `exclude_base_tools` is true, the SDK registers a tool named `read` that serves
only skill files:

- It is built from Tau's `create_read_tool_definition` with a path check passed as
  `ReadOperations.validate_path`, so it keeps the `path`, `offset`, and `limit`
  arguments, Tau's output limits, and its continuation hints. Its description says
  that it reads skill files only.
- The check resolves the requested path, following symlinks. It accepts a file
  inside the resolved directory of a skill Tau discovered for the session, or that
  skill's resolved `SKILL.md`. It rejects every other path with an input error,
  including other workspace files, system paths, `..` escapes, and symlinks that
  resolve outside the skill directory.
- It reads the session's skills when it is called, so a reload changes what it
  serves. It serves every discovered skill: `disable_model_invocation` keeps a skill
  out of Tau's index, as it does with the ordinary `read`, and an explicit
  `/skill:` invocation can still follow that skill's references.
- Because a tool named `read` exists, Tau adds its own skill index, whose wording
  already tells the model to resolve a skill's relative references against the skill
  directory. The SDK writes no skill prompt text. A test asserts the index, so a
  tau-ai change to the gate fails the suite.
- It is not a coding tool: the `base_tool_count` diagnostic stays `0`.

With base tools included, nothing changes: Tau's ordinary `read` serves skills.

A separate `read_skill(name, path)` tool with an SDK-written index was rejected. The
SDK can add prompt text only through `append_system_prompt`, which is fixed when the
session loads, appears before the project context, and would copy Tau's format.

### Managed deployment configuration

The repository's existing `harness_agent.spec.env_vars` sets these non-secret
process settings. For example:

```yaml
api_version: "2.3.0"
name: agent-runtime
resources:
  - key: agent
    kind: harness_agent
    spec:
      source_path: api/agent/main.py
      llm_provider: openai
      llm_model: gpt-5.4
      llm_thinking: medium
      env_vars:
        - name: TAU_EXCLUDE_BASE_TOOLS
          value: "true"
        - name: TAU_EXCLUDE_MAINSEQUENCE_MCP
          value: "true"
```

The platform's existing workflow accepts these portable, non-reserved names and passes them into
the deployed runtime's environment. No new workflow field, Agent Card field, backend setting, or
deployment adapter is required. SDK setting and catalog tests cover the runtime.

### Version-matched project-customization skill

Implementing these settings requires updating the packaged
`tau-project-customization` skill under `src/ms_tau_sdk/agent_skills/`. This is the
skill that owns guidance for project tools and effective Tau behavior. It must
explain where to set the two process variables, their `false` defaults, the four
combinations, and how project tools registered in `.tau/extensions` compose with
the optional coding tools, the skill-scoped `read` that replaces them, optional
Main Sequence MCP integration, and always-present A2A Task controls. It must state that prompts and Agent Card
skills do not remove executable tools. `ms-tau skills sync` then supplies that
version-matched guidance to consuming projects.

The SDK reference settings and project-configuration guides must use the same
definitions. Platform-owned workflow skills continue to own deployment schema
and `env_vars` validation.

## Consequences

- Existing deployments retain the current catalog because both settings default
  to `false`.
- A project can deploy a tool-only agent with both settings `true`, while retaining
  the two controls for its own A2A Tasks.
- With base tools excluded, project skills stay available: the model can read skill
  files and no other file. A tool named `read` remains in the catalog, so diagnostics
  and documentation describe it as skill-scoped.
- Disabling the model-facing coding tools is not an operating-system sandbox.
  Project extension code still runs in the project's Python process and identity.
- The deployment author controls these workflow values. A platform-enforced
  restriction against that author would require a separate platform-owned policy.

## Implementation checks

- Test all four combinations in both managed and local session construction.
- Assert that MCP connection is never attempted when excluded, including during
  startup and cold session load, while backend HTTP and incoming A2A still work.
- Assert that the Task controls remain present and cannot be overridden by a
  project extension, including after reload.
- Assert that both exclusions yield only project-registered tools plus the
  skill-scoped `read` and the Task controls, and that the effective prompt does not
  mention excluded capabilities.
- With base tools excluded, assert that the prompt contains Tau's skill index with
  the packaged and with a project system prompt, that the skill-scoped `read` returns
  a skill's `SKILL.md` and a referenced file inside the skill, that it rejects a
  workspace file, a system path, a `..` escape, and a symlink leaving the skill, and
  that an extension registering `read` fails on load and after reload.
- Keep the current default-catalog tests. Check that the packaged
  `tau-project-customization` skill, reference documentation, and explicit
  skill-sync output describe the new settings and their managed workflow
  placement when implementation lands.
