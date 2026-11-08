# ADR 0026: Per-Agent Runtime Resources From the Workflow Declaration

Status: Accepted — implemented in the SDK; the platform side is pending

Date: 2026-11-08

Amends:

- [ADR 0003: Tau-native project configuration](./0003-tau-native-project-configuration.md). Project
  resources and extensions stay Tau-native, but the platform may select, for each Agent, which of
  the project's `.tau` resources its sessions load.
- [ADR 0011: Independent base-tool and Main Sequence MCP exclusion](./0011-independent-base-tool-and-main-sequence-mcp-exclusion.md).
  The Agent's declaration composes project tools by selecting extensions. The selection names
  extensions Tau already discovers and adds no tool definitions, so it is not the SDK tool
  manifest that ADR rules out.
- [ADR 0025: The runtime reads its resources only from `.tau` by default](./0025-runtime-resources-only-from-tau.md).
  `.tau/` stays the only project directory. A selection narrows what is read inside it, and the
  `TauPaths` of section 2 also restrict the skill listing.

## Context

A repository branch may declare several `harness_agent` resources. Each one deploys its own Agent
from the same code and image, with its own Agent Card, MCP applications and grants. A repository
uses this to deploy, for example, a full assistant and a narrower Agent that sees only some of the
project's tools and has its own instructions.

Today the runtime gives every Agent the whole `.tau/` directory: `.tau/SYSTEM.md`, every skill in
`.tau/skills` and every extension in `.tau/extensions`. The settings that compose a runtime are
process-wide environment settings (ADRs 0011 and 0025). They are untyped, the platform does not
validate them, and they are not part of the Agent's declaration. Two Agents built from one
repository therefore cannot differ in prompt, skills or project tools.

`tau-ai==0.4.2` already exposes what a per-Agent composition needs, through the
`CodingSessionConfig` and `TauResourcePaths` the SDK builds for every session:

- `custom_system_prompt` is an explicit startup value. With it, Tau ignores `.tau/SYSTEM.md` and
  the packaged `SYSTEM.md` and reports each as an informational diagnostic.
- `project_extensions_enabled=False` with `extension_paths` loads only the listed extension files
  or folders. Tau marks them `source="explicit"`, and a missing path is only a warning
  diagnostic.
- `skills_enabled` turns all skills on or off. Project skills come from the directories the SDK's
  `TauPaths` return, and the SDK already controls their listing (ADR 0025, section 2).

Tau gives the session each extension's tools, hooks, commands and prompt sections together. It
has no option to drop single tools of a loaded extension.

## Decision

### 1. The selection comes from session bootstrap

Session bootstrap returns `runtime_resources`, which the platform resolves from the Agent's
workflow declaration (`harness_agent.spec.runtime_resources`):

| Field | Value | When omitted |
| --- | --- | --- |
| `system_prompt` | workspace-relative path of a UTF-8 Markdown file under `.tau/`, outside `.tau/skills/`, `.tau/prompts/` and `.tau/extensions/` | Tau's precedence: `.tau/SYSTEM.md`, else the packaged default |
| `skills` | list of skill names (a skill's name is its folder's name, ADR 0025, section 1) | every project skill |
| `extensions` | list of entries directly under `.tau/extensions/`: extension files or extension folders | every project extension |

An absent `runtime_resources`, or an absent field, keeps today's behavior for that resource. An
empty list loads none. The selection applies to every session of the Agent, on load and on
reload. No process setting, request, session or model response sets or changes it.

### 2. System prompt

The SDK reads the selected file from the workspace and passes its content as
`custom_system_prompt`. Session load fails before any model call when the file is missing,
unreadable, not UTF-8, a symbolic link, or outside the allowed part of `.tau/`. Tau keeps an
explicit prompt for the life of the session, so a reload keeps the content read on load. A
deployed workspace does not change between the two.

`.tau/APPEND_SYSTEM.md` and `.tau/AGENTS.md` still apply. They are the branch-wide instructions
shared by every Agent of the repository. The prompt the SDK appends itself, such as the Main
Sequence MCP resource prompt, is unchanged.

### 3. Skills

The `TauPaths` of ADR 0025 list only the selected skill folders, whichever group folder holds
them. An empty list sets `skills_enabled=False`. Session load fails when a selected name matches
no skill folder.

The skill-scoped `read` of ADR 0011 and `/skill:<name>` work only on the loaded skills, so they
follow the selection without further change. The skills of declared applications (ADR 0023) come
with `mcp_applications` and are not affected.

### 4. Extensions and their tools

With a selection, the SDK sets `project_extensions_enabled=False` and passes the selected
entries, resolved under `.tau/extensions/`, as `extension_paths`. An empty list loads no project
extension.

- Everywhere the SDK checks project extensions, the selected entries count as the project's
  extensions: the tool-only catalog check of ADR 0011, the extension source view of ADR 0012,
  extension diagnostics and counts. The source view still accepts only files under
  `.tau/extensions/`.
- Session load fails when a selected entry does not exist, is a symbolic link, or produces Tau's
  "no extensions found in explicit extension path" diagnostic. The SDK checks each entry against
  Tau's own project discovery before any project code runs, so an entry Tau would not discover in
  `.tau/extensions/` fails too. After Tau composes the session, each selected entry must have
  loaded with no error diagnostic, and no other project extension may have loaded. A selection
  never silently yields a different catalog.
- The catalog checks of ADR 0011 run unchanged after Tau composes the tools. The reserved names
  stay reserved.

The unit of tool selection is the extension. A repository that gives different Agents different
tools puts each group of tools in its own extension.

### 5. Older and newer counterparts fail closed

The bootstrap response model accepts unknown fields, so a runtime that predates this ADR would
ignore a selection and load the whole `.tau/`. The bootstrap request therefore lists
`supported_runtime_resources_schema_versions: [1]`, following
`supported_snapshot_schema_versions` and `supported_provider_control_schema_versions`. The
platform refuses to bootstrap an Agent that declares `runtime_resources` for a runtime that does
not list version 1.

The SDK validates `runtime_resources` strictly. An unknown field or a malformed value fails
session load.

### 6. The selection is visible

`runtime.project_resources.load.completed` and the agent inspection payload's `resources` (ADR
0025, section 5) also report the selection: the selected prompt path, skill names and extension
entries. Paths are relative to the workspace.

### 7. Process settings keep their meaning

`TAU_EXCLUDE_BASE_TOOLS`, `TAU_EXCLUDE_MAINSEQUENCE_MCP` and `TAU_INCLUDE_DEVELOPMENT_RESOURCES`
are unchanged. When `TAU_INCLUDE_DEVELOPMENT_RESOURCES` admits `.agents/skills`, a skill selection
applies to every project skill directory the scope reads.

### Not decided here

- **Local development.** Local `ms-tau` has no platform bootstrap and keeps loading the whole
  `.tau/`. How a developer runs one declared Agent locally, for example by naming its declaration
  key, is a separate decision.
- **Prompt templates.** `.tau/prompts` stays shared by every Agent of the repository.

## Rejected alternatives

### Select single tools by name

Tau 0.4.2 cannot drop single tools of a loaded extension. The SDK would have to patch Tau's
extension runtime across a private boundary, and the hooks, commands and prompt sections of the
dropped tools' extension would stay active.

### Process environment settings

One process serves one Agent, so environment settings could carry a selection. But their names
would be untyped conventions the platform cannot validate, and the Agent's definition would be
split between its declaration and its environment.

### A configuration directory per Agent, such as `.tau/agents/<name>/`

Tau reads one `.tau` directory. A second, name-keyed directory would be an SDK-specific
configuration model of the kind ADR 0003 rules out, and renaming an Agent would silently change
what it loads.

## Consequences

- A repository can deploy several Agents from one codebase, each with its own system prompt,
  skills and project tools.
- Repositories group tools into extensions by the Agents that use them.
- Every Agent of a repository still runs the same code. A selection limits what the model is
  given, not what is installed. Separating authority between Agents is the job of the platform's
  per-Agent grants.

## Implementation and acceptance

- The session loader builds `custom_system_prompt`, the skill listing, `project_extensions_enabled`
  and `extension_paths` from the bootstrap selection, on load and reload. Without a selection,
  every path is unchanged.
- Tests cover each field omitted, empty and set; each failure in sections 2 to 5; reload keeping
  the selection; the ADR 0011 catalog check with selected extensions; and the inspection payload
  and load log.
- The bootstrap request carries `supported_runtime_resources_schema_versions`.
- The runtime contract reference and the project configuration guide describe the selection, and
  the packaged `tau_project_customization` skill explains how to split tools into extensions.

Implemented in the SDK for 2.0.30: `ms_tau_sdk.resources.selection` validates and resolves the
selection, the session loader passes it to Tau, and `tests/unit/test_runtime_resource_selection.py`
loads real Tau sessions for every case above. The
[multiple Agents guide](../guides/multiple-agents-one-repository.md) documents it with a worked
example. The platform side, which accepts `harness_agent.spec.runtime_resources`, returns it at
bootstrap and refuses runtimes that do not list schema version 1, is pending.
