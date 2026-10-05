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

Tau discovers project-owned resources from the workspace, including:

```text
.tau/
├── SYSTEM.md
├── prompts/
├── skills/
└── extensions/
```

Project resources participate in Tau diagnostics and reload. Extension session-start, tool hooks,
and session-shutdown callbacks use Tau's normal lifecycle.

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

## Optional tools

Web, search, fetch, video, and runtime-information tools are not part of the base SDK. A project may
install its chosen implementation and register tools in `.tau/extensions`. Those tools do not alter
the SDK's own Main Sequence HTTP transport.

## Runtime tool composition

The host process sets two independent booleans, both `false` by default:

| Environment variable | When `true` |
| --- | --- |
| `TAU_EXCLUDE_BASE_TOOLS` | Replace Tau's `read`, `write`, `edit`, and `bash` with a `read` that serves only skill files. |
| `TAU_EXCLUDE_MAINSEQUENCE_MCP` | Do not connect to Main Sequence MCP or inject its tools, resources, or resource prompt. |

Project extension tools remain in either mode. `task_request_input` and
`task_request_authorization` always remain for the agent's own A2A Task and cannot be replaced by
a project tool. Set these variables in `harness_agent.spec.env_vars` for managed deployments, or in
the process environment for local `ms-tau`; Python applications can set the matching
`TauSDKSettings` fields. With both exclusions enabled, the catalog consists of project tools, the
skill-scoped `read`, and the two Task controls. If only one is enabled, the other built-in source
remains. A project prompt or Agent Card skill description does not remove tools.

With base tools excluded, project skills in `.tau/skills/` stay available. The skill-scoped `read`
opens a discovered skill's `SKILL.md` and the files in its directory, such as `references/*.md`,
and rejects every other path, including symlinks that resolve outside the skill. Because a tool
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


## Named providers and sharing

The existing `MainSequenceClient.update_session_config`,
`hydrate_provider_credential`, and `hydrate_local_provider_credential` methods
accept `custom_id=None`. Omission keeps existing request bodies. To select a
configured record, pass its name, for example `custom_id="openai-work"`.
The provider integration key remains unchanged. Explicit names must match the
backend response; a missing or substituted selection fails.

Local setup accepts `TAU_LOCAL_CUSTOM_ID` and
`TAU_LOCAL_ORGANIZATION_ENVIRONMENT_UID`. Both are optional. Local sessions
persist the resolved credential UID and Environment alongside their model
selection. Resume and refresh retain that binding; changing an idle session's
selection uses the existing session-model operation. Local SQLite upgrades add
nullable columns without changing existing session identifiers or schema versions.

`AgentSession.custom_id`, `model_provider_credential_uid` and
`organization_environment_uid` expose the safe selected identity. Hydrated
`ProviderCredential` also carries `owner_user_uid`. Old server responses may
omit these fields. Custom endpoints retain their existing identifier and
configuration envelope and do not require `custom_id`.

Sharing a configured provider lets recipients receive and copy its credentials
in their own runtime, including local Tau. Only share with people and workload
operators you trust. Their usage counts against the provider quota or billing
associated with those credentials. Removing access stops future credential
retrieval; credentials already received may work until they expire or are revoked
at the provider. Never include keys or tokens in model-visible messages, logs,
or ordinary metadata responses.
