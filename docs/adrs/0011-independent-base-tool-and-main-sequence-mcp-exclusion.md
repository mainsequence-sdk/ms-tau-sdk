# ADR 0011: Independent Base-Tool and Main Sequence MCP Exclusion

Status: Accepted; implemented

Date: 2026-09-24

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
| `exclude_base_tools` | `TAU_EXCLUDE_BASE_TOOLS` | Do not create Tau's `read`, `write`, `edit`, or `bash` tools. |
| `exclude_mainsequence_mcp` | `TAU_EXCLUDE_MAINSEQUENCE_MCP` | Do not connect to Main Sequence MCP or inject its tools, resources, or resource prompt. |

The settings are fixed when the application process starts. They apply to every
session in that process, in managed and local mode. A request, Agent Card, session,
or model response cannot change them. Python applications may pass an explicit
`TauSDKSettings` instance to `create_app`; the `ms-tau` command reads the same
environment settings. These two `TAU_` names are exceptions to the earlier
`MAINSEQUENCE_TAU_*` naming convention because Django reserves the
`MAINSEQUENCE_` prefix in repository workflow environment variables.

The resulting model-facing catalog is:

| Exclude base tools | Exclude Main Sequence MCP | Effective sources |
| --- | --- | --- |
| No | No | Four coding tools, Main Sequence MCP, project tools, and A2A Task controls. |
| Yes | No | Main Sequence MCP, project tools, and A2A Task controls. |
| No | Yes | Four coding tools, project tools, and A2A Task controls. |
| Yes | Yes | Project tools and A2A Task controls only. |

`task_request_input` and `task_request_authorization` are unconditional protocol
tools. Neither setting excludes them. A project extension must not replace them:
session loading or extension reload fails if a project registers either reserved
name. This explicit check is necessary because Tau otherwise permits an extension
tool to override an existing tool of the same name.

Project tool registration remains Tau-native through `.tau/extensions`. Both
settings leave project resource and extension discovery enabled; they do not add
an SDK tool manifest or turn Agent Card skill descriptions into executable tool
declarations. The effective catalog must be checked after Tau composes tools on
session load and after extension reload. In the two-exclusions case, every tool
other than the two Task controls must come from a project extension. Unexpected
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
packaged default. With `read` excluded, Tau 0.4.2 does not insert discovered Tau
skills into the system prompt; projects using that mode must provide needed
guidance through their effective system prompt or a declared retrieval tool.

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

Django's existing workflow accepts these portable, non-reserved names. The
verified path is `env_vars` validation and normalization, backing Job custom
environment persistence, `Job.get_environment()`, ResourceRelease deployment
environment construction, Harness Agent Knative construction, and the container
environment list. No new workflow field, Agent Card field, backend setting, or
deployment adapter is required. Verification against `tdag-django` commit
`51e96e8fb2bcf09cb6124ad2cd4287bfd3630d01` directly accepted both exact
variable names and passed four focused workflow, Job-environment, and Harness
Agent deployment tests on 2026-09-24. This verifies the generic environment
path; SDK setting and catalog tests cover the runtime.

### Version-matched project-customization skill

Implementing these settings requires updating the packaged
`tau-project-customization` skill under `src/ms_tau_sdk/agent_skills/`. This is the
skill that owns guidance for project tools and effective Tau behavior. It must
explain where to set the two process variables, their `false` defaults, the four
combinations, and how project tools registered in `.tau/extensions` compose with
the optional coding tools, optional Main Sequence MCP integration, and
always-present A2A Task controls. It must state that prompts and Agent Card
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
- Assert that both exclusions yield only project-registered tools plus Task
  controls, and that the effective prompt does not mention excluded capabilities.
- Keep the current default-catalog tests. Check that the packaged
  `tau-project-customization` skill, reference documentation, and explicit
  skill-sync output describe the new settings and their managed workflow
  placement when implementation lands.
