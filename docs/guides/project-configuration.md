# Project Tau Configuration and Extensions

The project `.tau` directory is the only agent-behavior configuration boundary. Main Sequence TAU
SDK passes its packaged defaults and workspace to Tau; Tau resolves one effective configuration.

## General behavior override

Without a project override, the packaged SDK `SYSTEM.md` supplies concise agent instructions.
To replace it, create:

```text
.tau/SYSTEM.md
```

The project file is the effective general system prompt. The SDK does not require a companion file
and does not maintain a separate prompt setting. Other Tau-native resource forms continue to follow
the Tau version pinned by the SDK.

## Project resources

The runtime reads the Agent's resources from the workspace `.tau/` directory:

```text
.tau/
├── SYSTEM.md            replaces the packaged system prompt
├── APPEND_SYSTEM.md     appended to the system prompt
├── AGENTS.md            project instructions for the Agent
├── prompts/             prompt templates
├── skills/              the Agent's skills
└── extensions/          project extensions
```

Project resources participate in Tau diagnostics and reload. Extension session-start, tool hooks,
and session-shutdown callbacks use Tau's normal lifecycle.

## Two sets of instructions

A repository that deploys an Agent holds instructions for two different agents. `.tau/` is the
deployed Agent's: the assistant people talk to. The repository root's `AGENTS.md` and `.agents/`
are the coding agents': `ms-tau skills sync` writes `.agents/skills/ms_tau_sdk/`, the mainsequence
CLI writes `.agents/skills/mainsequence/`, and the repository adds its own development skills. The
runtime reads nothing from the second set by default: no `AGENTS.md` outside `.tau`, no
`.agents/skills`, no `.agents/prompts`. Instructions meant for the deployed Agent belong in
`.tau/AGENTS.md`, `.tau/APPEND_SYSTEM.md` or `.tau/SYSTEM.md`. The one `.agents` file the SDK
reads is `.agents/agent_card.json`, the Agent's platform identity, which local mode serves at
`GET /api/local/v1/agent`; Tau does not read it.

An Agent that is a coding agent over its own repository wants the second set: set
`TAU_INCLUDE_DEVELOPMENT_RESOURCES=true` and Tau reads the repository as a coding harness, the
root `AGENTS.md` and every `AGENTS.md` above the workspace, `.agents/AGENTS.md`, `.agents/skills`
and `.agents/prompts`, beside `.tau`. See [runtime composition](#runtime-tool-composition) and
[ADR 0025](../adrs/0025-runtime-resources-only-from-tau.md).

## Project skills

Tau reads the Agent's skills from `.tau/skills/`. A folder holding a `SKILL.md` is a skill; a
folder without one groups skills, at any depth, such as `.tau/skills/<group>/<skill>/SKILL.md`. A
folder inside a skill is not a skill, and a linked group folder is not followed. A skill's name is
its folder's name. When two skills in the folder share a name, the first in path order is kept and
Tau reports the other. A chat user can invoke any of these skills with `/skill:<name>`.

An application that publishes its Agent's usage skills to other agents with the mainsequence SDK's
`skills_extension` points it at `.tau/skills` itself: the Agent's skills and the published skills
are one set. Do not publish a group folder inside it to keep some skills private; instructions
other agents may not see belong in `.tau/SYSTEM.md` or `.tau/AGENTS.md`. The publisher's rules
then hold for the whole folder: lowercase letters, digits and single hyphens in every skill and
group folder name; every folder a skill or a non-empty group; a skill's frontmatter `name` equal to
its folder's name; leaf folder names unique, because Tau names a skill by its folder. A group folder
prefixes the published `skill://` URIs, so a pack that was published from its own folder moves its
skills directly into `.tau/skills` to keep its URIs. Development skills under `.agents/skills` are
never published.

## Ownership and trust

The SDK enables and trusts the selected project's Tau resources because the project already owns
arbitrary Python code in the same process. Project authors are responsible for:

- extension source and behavior;
- optional tools and their Python/system dependencies;
- outbound network access performed by extensions;
- compatibility with the pinned Tau and SDK versions; and
- testing reload and shutdown behavior.

The SDK does not sandbox project extensions. It continues to protect runtime credentials,
authenticated caller and lease proof, persistence ordering, secret redaction, and wire validation.
The [Security and access guide](../reference/security-model.md#what-the-agents-code-can-reach) lists
what code in the Agent's process can reach.

## Optional tools

Web, search, fetch, video, and runtime-information tools are not part of the base SDK. A project may
install its chosen implementation and register tools in `.tau/extensions`. Those tools do not alter
the SDK's own Main Sequence HTTP transport.

## Runtime tool composition

The host process sets three independent booleans, all `false` by default:

| Environment variable | When `true` |
| --- | --- |
| `TAU_EXCLUDE_BASE_TOOLS` | Replace Tau's `read`, `write`, `edit`, and `bash` with a `read` that serves only skill files. |
| `TAU_EXCLUDE_MAINSEQUENCE_MCP` | Do not connect to Main Sequence MCP or inject its tools, resources, or resource prompt. |
| `TAU_INCLUDE_DEVELOPMENT_RESOURCES` | Read the repository as a coding harness: the root `AGENTS.md` and every `AGENTS.md` above the workspace, `.agents/AGENTS.md`, `.agents/skills` and `.agents/prompts`, beside `.tau`. |

Project extension tools remain in either mode. `task_request_input` and
`task_request_authorization` always remain for the agent's own A2A Task and cannot be replaced by
a project tool. Set these variables in `harness_agent.spec.env_vars` for managed deployments, or in
the process environment for local `ms-tau`; Python applications can set the matching
`TauSDKSettings` fields. With both exclusions enabled, the catalog consists of project tools, the
skill-scoped `read`, and the two Task controls. If only one is enabled, the other built-in source
remains. A project prompt or Agent Card skill description does not remove tools.

With base tools excluded, project skills stay available. The skill-scoped `read`
opens a discovered skill's `SKILL.md` and the files in its directory, such as `references/*.md`,
and rejects every other path, including symlinks that resolve outside the skill and every path
under `.agents/skills`. Because a tool
named `read` exists, Tau still lists the skills in the system prompt. A project extension cannot
register `read` in this mode.

## Inspect and test project tools locally

After local Tau has loaded a session, Tau Board's Agent tab shows the effective Agent Card, tools
grouped by runtime source, project extension diagnostics, and the registered extension entry
source. Project-extension tools with an object JSON Schema get a generated input form plus a raw
JSON fallback. Validate the canonical arguments, confirm the visible side-effect warning, and run
the exact loaded tool without starting a model turn or adding conversation or Task history.

The workbench is not a sandbox or dry-run layer. The tool executes with the Tau process's real
filesystem, network, environment, and credential access. Coding tools, Main Sequence MCP tools,
and A2A Task-control tools are visible but cannot be executed from this project-tool workbench.


## Named providers and policies

A built-in provider can have several configured credentials in an Environment. Choose a record by
its `custom_id`, such as `openai-work`; this is a name, not a credential value. The provider name
and model name remain unchanged. For local setup, use the existing settings:

```bash
export TAU_LOCAL_CUSTOM_ID=openai-work
export TAU_LOCAL_ORGANIZATION_ENVIRONMENT_UID="<your-environment-uid>"
```

Replace the Environment placeholder before running the commands. Both settings are optional.
Without an Environment, the platform uses an accessible active Environment or the sole accessible
one; if neither is unambiguous, select one explicitly. Without a credential name, a new session
prefers your original owned credential, then the oldest record you hold a policy on; for an
Organization admin, the first record anyone added in that Environment. An explicit name selects
your own record of that name first; otherwise it must match exactly one accessible record in that
Environment. Custom endpoints keep their provider identifier and do not use `custom_id`.

Meta (`meta`, default model `muse-spark-1.3`) has no interactive sign-in: each person stores their
own Meta Model API key as a `meta` credential, and sessions select and use it like any other
credential. Running Meta needs ms-tau-sdk 2.0.27 or later; see
[provider troubleshooting](../reference/troubleshooting.md#a-provider-is-not-in-this-runtimes-tau-catalog).

The session records its resolved credential and Environment. Resume and refresh keep that selection;
changing an idle session's model or configured provider uses the session-model operation. A revoked
or unavailable selection fails rather than silently switching credentials. Check the selected name,
owner, and Environment before use. See the [settings reference](../reference/settings.md#authenticated-local-development)
and [provider troubleshooting](../reference/troubleshooting.md#local-provider-hydration-is-rejected).

Policies on each record decide who may use and manage it. A policy can be held directly or through
a Team, and Organization admins hold every action on their Organization's records:

- **A credential** (a person's sign-in to a built-in provider, named by its `custom_id`): `user`
  selects it and receives it in sessions and workloads, including local Tau; `editor` also refreshes
  it, signs in to the provider again to reconnect it, revokes it, and sees who has access; `owner`
  also decides who has access. The credential's person holds `owner`.
- **A custom endpoint** (an Environment's own inference endpoint and models): `user` sends requests
  through it and its models; `editor` also changes its endpoint, authentication and models, deletes
  it, and sees who has access; `owner` also decides who has access. Its creator holds `owner`.

In a session, the platform checks these policies for the session owner, never for the Agent's
workload: local Tau uses the records you may use, and a person's session on a hosted Agent uses the
records that person may use. A policy given only to the Agent's workload does not reach people's
sessions; to let several people run an Agent on one record, its owner gives `user` to them or to a
Team they belong to.

Owners give and remove these policies in Command Center, in the provider settings' Access panel, or
through `/api/v1/model-provider-credentials/<uid>/access/` and
`/api/v1/custom-model-providers/<uid>/access/`. Anyone can remove their own policy, whoever manages
a workload can give it a policy they hold, `access/me/` answers what you may do with a record, and
editors and owners see who holds which policy. A workflow file's `access.model_providers` entry
gives its workload the policy it names, usually `policy: user`, and works when the person behind
the push can do everything that policy allows.

A refused change answers `403` with the code `missing_action`, the `required_action` (for example
`model-provider-credentials.revoke` when you hold only `user`) and the `granting_policies` that
include it: ask an owner for one of them, or ask an editor to make the change. A record you cannot
see answers `404`.

Giving `user` or more on a credential or custom endpoint lets the holder receive and copy its
credentials in their own runtime, including local Tau. Only give it to people and workload
operators you trust. Their usage counts against the provider quota or billing associated with those
credentials. Removing a policy stops future credential retrieval; credentials already received may
work until they expire or are revoked at the provider. Organization admins need no policy: they can
receive and use the credentials of any configured provider in the Organization, and their usage
counts against it too. An admin with none of their own for a provider gets the first one added in
that Environment. Never include keys or tokens in model-visible messages, logs, or ordinary
metadata responses. The [Security and access guide](../reference/security-model.md#model-providers)
summarizes these rules, and
[what the Agent's code can reach](../reference/security-model.md#what-the-agents-code-can-reach)
explains why trusted runtime code can reach delivered provider credentials.
