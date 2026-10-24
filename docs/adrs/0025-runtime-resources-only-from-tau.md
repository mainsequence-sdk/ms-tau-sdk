# ADR 0025: The Runtime Reads Its Resources Only From `.tau` by Default

Status: Accepted — implemented

Date: 2026-10-24

Amends:

- [ADR 0003: Tau-native project configuration](./0003-tau-native-project-configuration.md) by
  making `.tau/` the only project directory the runtime reads by default, as that ADR already
  states, by naming the one Tau behavior the SDK intercepts to keep it so, and by adding the host
  setting that admits the repository's development resources.
- [ADR 0006: SDK-owned agent development skills](./0006-sdk-owned-agent-development-skills.md),
  section 5, by enforcing it: the synchronized development skills, and every other `.agents/`
  resource, do not become runtime configuration.
- [ADR 0011: Independent base-tool and Main Sequence MCP exclusion](./0011-independent-base-tool-and-main-sequence-mcp-exclusion.md)
  by narrowing what the skill-scoped `read` serves to the skills under `.tau/skills`.

Supersedes the part of release 2.0.15
([#99](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/99)) that made the runtime read
`.agents/skills/ms_tau_sdk` and `.agents/skills/mainsequence`. Reading skills nested in group
folders stays, under `.tau/skills`.

## Context

A repository that runs `ms-tau-sdk` holds two sets of instructions for two different agents:

- **The deployed agent's resources.** `.tau/SYSTEM.md`, `.tau/skills`, `.tau/prompts` and
  `.tau/extensions` define the Agent the repository deploys: the product assistant that people
  talk to through chat and A2A.
- **The development resources.** The root `AGENTS.md` and `.agents/skills/` tell a coding agent
  (Codex, Claude Code, a developer's Tau) how to work on the repository. `ms-tau skills sync`
  writes `.agents/skills/ms_tau_sdk/`; the mainsequence CLI writes `.agents/skills/mainsequence/`
  and `.agents/skills/mainsequence_platform/`; other SDKs write their own namespaces; the
  repository adds its own, such as `<project>_development`.

Tau is a coding-agent harness, and reads both sets the way Codex and Claude Code do: the root
`AGENTS.md` and every `AGENTS.md` between the project root and the workspace as project
instructions, and `.agents/skills` and `.agents/prompts` as resources, beside `.tau`. The SDK
points Tau at the Agent's own repository with `trust_override="approve"`, so everything is read.
Before 2.0.15 the namespace folders under `.agents/skills` were skipped by accident, because they
hold no `SKILL.md`; 2.0.15 made them load on purpose.

Measured on a repository that deploys a CRM assistant (SDK 2.0.23, `tau-ai==0.4.2`):

- 36 skills reach the agent; 1 is from `.tau/skills`. The other 35 are development skills:
  `ms_tau_sdk`, `mainsequence`, `metatables`, the repository's own `*_development` skills, and
  the usage pack the repository publishes to *other* agents over MCP.
- The system prompt is about 39,000 characters. The Agent's own definition, `.tau/SYSTEM.md` and
  its skill, is about 4,200 of them.
- `.tau/SYSTEM.md` says the Agent is a CRM assistant and must not offer repository editing or
  deployment. Directly after it, Tau injects the 15,000-character root `AGENTS.md`: "You are a
  dual-mandate agent", pre-push gates, migration decisions, release procedures.

This is not only noise. With the default coding tools the deployed agent has `bash` and skills
that teach migrations and releases. Any chat user can type `/skill:<name>`, which Tau expands from
user input against every loaded skill. With `TAU_EXCLUDE_BASE_TOOLS` the skill-scoped `read`
still serves every loaded skill's files, so the locked-down composition exposes the whole
development bundle. `.agents/skills` loads after `.tau/skills` and a later skill of the same name
replaces the earlier one silently: a development skill can shadow a runtime skill. None of it is
visible: the SDK logs and inspects extension diagnostics only, never which `SYSTEM.md`, context
files or skills were selected.

The SDK's own documents contradict each other. ADR 0003 and the project configuration guide call
`.tau/` the only agent-behavior boundary; the same guide then says Tau also reads
`.agents/skills`. ADR 0006 section 5 says the synchronized skills do not become runtime
configuration; the packaged `tau_project_customization` skill says `.agents/skills/ms_tau_sdk/`
"must not be used as application configuration" and, in the next paragraph, that it is read too.

Tau 0.4.2 offers no option for this. `project_resources_enabled=False` removes `.tau` as well.
`CodingSessionConfig.skills_enabled=False` removes every skill. `context_files` adds files and
removes none. The extension API adds prompt guidelines and sections and removes nothing.
`CodingSessionConfig.system` replaces the whole prompt and loses the skills index, extension
sections and reload. Skill and prompt directories go through `TauPaths`, which the SDK already
subclasses; the `AGENTS.md` lookup computes the ancestors of the workspace directly and does not.

Not every deployment is a product assistant. An Agent can be a coding agent over its own
repository, and for that Agent the root `AGENTS.md` and `.agents/` are the right instructions:
Tau's native scope is then wanted, on purpose. The reading scope is therefore a host decision, of
the same kind as the tool composition of ADR 0011, with a default that fits the common deployment.
The SDK makes that decision for the sessions it builds, and the fix is not taken upstream.

## Decision

### 1. `.tau` is the runtime's only project directory, by default

Unless the host sets `TAU_INCLUDE_DEVELOPMENT_RESOURCES` (section 6), the runtime reads the
Agent's resources from the workspace `.tau/` directory and nowhere else in the repository:

```text
.tau/
├── SYSTEM.md            replaces the packaged system prompt
├── APPEND_SYSTEM.md     appended to the system prompt
├── AGENTS.md            project instructions, injected as <project_instructions>
├── prompts/             prompt templates
├── skills/              the Agent's skills; a folder without SKILL.md groups skills
└── extensions/          project extensions
```

`.agents/` belongs to the coding agents that develop the repository. The runtime reads none of it,
and reads no `AGENTS.md` outside `.tau`: not the repository root's, not a parent directory's, not
`.agents/AGENTS.md`. The one exception is `.agents/agent_card.json`, the platform-owned identity
file that local mode's `GET /api/local/v1/agent` reads; Tau does not read it, and it stays where
the platform expects it.

Skills nested in group folders keep working, under `.tau/skills`. A skill's name remains its
folder's name. Tau's precedence among the directories it still reads is unchanged.

### 2. Skills and prompt templates: through the SDK's `TauPaths`

The SDK's `TauPaths` subclass returns the `.tau` location for every `.agents` project location Tau
asks for: `.tau/skills` for the project `.agents/skills` directory, `.tau/prompts` for the project
`.agents/prompts` directory, and `.tau` for the project `.agents` directory. Tau de-duplicates
repeated directories, so each is read once. Tau rebuilds its resource paths on load and reload but
keeps this object, so the rule holds on both.

The packaged resources root and the user-level `~/.tau` and `~/.agents` are unaffected: the SDK
already reads no user-level resources (`agents_root=None`, `root` on the package).

### 3. Context files: a version-locked interception, as in ADR 0016

Tau discovers `AGENTS.md` files in `tau_coding.context.discover_project_context_with_diagnostics`
and calls it, on load and on reload, through the name bound in `tau_coding.session`. The SDK wraps
that binding: when the session's `TauPaths` declare that they do not read the development
resources, the wrapper calls Tau's function and keeps only the files under the workspace `.tau/`
directory, reporting each removed file as an informational diagnostic; for any other caller it
returns Tau's result unchanged. Tau's own reading, diagnostics and de-duplication are reused;
nothing is reimplemented.

Like the provider patch of ADR 0016, the wrapper is locked to exactly `tau-ai==0.4.2`. With
another installed version, session construction fails with an instruction to review the patch
against that version, because an unverified patch could let development instructions reach a
deployed Agent silently. The wrapper is installed once per process, before the first session
loads, and applies to every session the process runs.

### 4. The Agent's skills are what it publishes

A repository that publishes its Agent's usage skills to other agents through the mainsequence
SDK's `skills_extension` points the publisher at `.tau/skills` itself: the Agent's skills and the
published skills are one set. It does not publish a group folder inside `.tau/skills` to keep
some of the Agent's skills private; instructions other agents may not see belong in
`.tau/SYSTEM.md` or `.tau/AGENTS.md`, which are never published. The publisher's rules therefore
hold for the whole folder: every skill and group folder is named with lowercase letters, digits
and single hyphens; a folder is a skill (it holds `SKILL.md`) or a non-empty group; a folder inside
a skill belongs to that skill; a skill's frontmatter `name` equals its folder's name. Tau names a
skill by its folder whatever group it is in, so leaf folder names are unique across the folder. A
group folder prefixes the published `skill://` URIs, so a pack that was published from its own
folder moves its skills directly into `.tau/skills` to keep its URIs. Development skills stay in
`.agents/skills` and are never published, in either reading scope. The SDK's runtime does not
enforce these rules; the publisher does, when the application starts, and the packaged
`tau_project_customization` skill carries them.

### 5. The selected resources are visible

On session load and reload, the SDK logs what Tau selected, as
`runtime.project_resources.load.completed`: the reading scope, the system prompt files, the
context files and the numbers of skills and prompt templates. It logs Tau's resource
diagnostics, among them a skill that overrides another, a duplicate skill name, the selected
system prompt file and an `AGENTS.md` the default scope removed, as
`runtime.project_resources.diagnostic`, beside the extension diagnostics it logs today. Paths are
relative to the workspace. The agent inspection payload carries the same selection under
`resources`, so a repository author can see what the Agent was given.

### 6. A coding Agent reads the whole repository on request

`TAU_INCLUDE_DEVELOPMENT_RESOURCES` (`TauSDKSettings.include_development_resources`, default
`false`) admits Tau's native scope: the repository root's `AGENTS.md`, every `AGENTS.md` between
the project root and the workspace, `.agents/AGENTS.md`, `.agents/skills` and `.agents/prompts`,
beside `.tau`. It is for an Agent that is a coding agent over its own repository. Like the two
exclusion settings of ADR 0011 it is process-wide, applies to managed and local sessions alike,
is independent of them, and is set in `harness_agent.spec.env_vars` for a managed deployment, in
the process environment for local `ms-tau`, or on `TauSDKSettings` in a Python host. Skills nested
in group folders are read in both scopes. The skill-scoped `read` and `/skill:<name>` follow
whatever is discovered. The setting is reported in the runtime settings snapshot, the load log and
the agent inspection payload.

### Not decided here

The packaged default `SYSTEM.md` and the packaged `review-code-repository` prompt template are
written for a coding agent and reach every deployed Agent without a `.tau/SYSTEM.md`. Whether the
packaged defaults should describe a deployed Agent instead is a separate decision.

## Rejected alternatives

- **Building the system prompt in the SDK (`CodingSessionConfig.system`).** Loses Tau's skills
  index, extension prompt sections, and reload; the SDK would own prompt assembly against ADR 0003.
- **Disabling project resources (`project_resources_enabled=False`).** Removes `.tau` too.
- **Excluding the known development namespaces** (`ms_tau_sdk`, `mainsequence`, `metatables`, …)
  **and keeping the rest of `.agents/skills`.** The list is never complete, and a repository's own
  `*_development` skills would still reach the Agent.
- **Lowering the precedence of `.agents/skills` instead of removing it.** Still mixes the two
  sets; shadowing was the smallest of the problems.
- **Reimplementing context discovery in the SDK instead of wrapping Tau's.** Owns behavior Tau
  owns; the wrapper reuses Tau's reading and diagnostics and changes one thing.
- **A Tau option, upstream.** Not in the pinned version; the reading scope is the SDK host's
  decision for the sessions it builds.
- **Keeping `.agents/skills` because the usage pack an application publishes lives there.** The
  pack is the Agent's own skills and belongs in `.tau/skills`; section 4 points the publisher
  there.
- **Publishing a group folder inside `.tau/skills` to keep some skills private.** Two sets again,
  decided per skill by each author; the Agent and its consumers drift apart, and private
  instructions already have a place in `.tau/SYSTEM.md` and `.tau/AGENTS.md`.
- **Inferring the scope from the repository**, for example reading `.agents` when `.tau` is
  absent. The deployed Agent's reading would change with an unrelated file; the host states it.
- **Making the scope the default for coding Agents too, with no setting.** A coding Agent over its
  own repository needs the root `AGENTS.md` and `.agents/`; forcing them into `.tau` duplicates
  the repository's development instructions.

## Consequences

- A repository whose Agent relied on skills under `.agents/skills` at runtime moves them to
  `.tau/skills` and, when it publishes them, points `skills_extension` at the new folder. This is
  a breaking change for those repositories and is called out in the changelog with the move.
- Instructions meant for the deployed Agent that lived in the root `AGENTS.md` move to
  `.tau/AGENTS.md`, `.tau/APPEND_SYSTEM.md` or `.tau/SYSTEM.md`. Most of a root `AGENTS.md` is
  development guidance and moves nowhere.
- The system prompt of the measured repository shrinks from about 39,000 characters to the
  Agent's own definition, about 4,200.
- `/skill:<name>` from chat and the skill-scoped `read` reach only the Agent's own skills.
- An Agent that is a coding agent over its own repository sets
  `TAU_INCLUDE_DEVELOPMENT_RESOURCES=true` to keep reading as before; nothing else changes for it.
- A second private Tau boundary is patched under a version lock. Version drift fails session
  construction visibly instead of changing what the Agent reads. Taking a new Tau release means
  verifying both patches against it, as ADR 0016 already requires.
- `ms-tau skills sync` and the mainsequence CLI keep writing to `.agents/skills`; nothing about
  the development workflow changes.

## Implementation and acceptance

1. Section 2. Tests: a skill under `.agents/skills/<namespace>/<skill>/SKILL.md` and a prompt
   template under `.agents/prompts` are not loaded; skills nested in group folders under
   `.tau/skills` are loaded, listed in the system prompt, reloaded and served by the skill-scoped
   `read`; a path under `.agents/skills` is refused by that `read`. The existing nested-skills test
   moves from `.agents/skills` to `.tau/skills`.
2. Section 3. Tests: a root `AGENTS.md`, an `AGENTS.md` in a parent directory of the workspace and
   `.agents/AGENTS.md` are absent from the system prompt; `.tau/AGENTS.md` is present; the result
   holds after a reload; with `tau-ai` reporting another version, session construction fails with
   the review instruction and no session is created.
3. Section 5. Tests: the selection and the removed `AGENTS.md` files are logged with
   workspace-relative paths and no absolute path; the agent inspection payload carries the
   `resources` selection.
4. Section 6. Tests: with the setting on, the root and parent `AGENTS.md`, `.agents/AGENTS.md`,
   the `.agents/skills` skills and the `.agents/prompts` templates are read, no `AGENTS.md` is
   reported as removed, and the skill-scoped `read` serves a development skill.
5. Sections 1, 4 and 6. In the same change, update the project configuration guide, the
   quickstart, the README, the settings and compatibility references, the Security and access
   guide, the packaged `tau_project_customization` and `tau_repository_integration` skills, and
   the changelog, with a migration note for repositories that kept runtime skills under
   `.agents/skills`.
6. ADR 0003, ADR 0006 and ADR 0011 carry an amendment note that points here.
