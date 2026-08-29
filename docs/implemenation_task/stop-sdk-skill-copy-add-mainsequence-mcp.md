# Use Main Sequence MCP Without SDK Runtime Coupling

Status: Implemented
Date: 2026-07-25
Repositories: `astro`, `tdag-django`

## Goal

Remove the Main Sequence SDK and CLI from the Astro runtime, stop copying SDK
skills into project repositories, and automatically include the existing Main
Sequence MCP in Tau sessions.

The final ownership model is:

- the project repository owns project-specific `AGENTS.md`, skills, and prompts
- Django `/mcp` owns general Main Sequence knowledge and platform operations
- Astro automatically exposes Django MCP tools and resources to every durable
  Tau coding session
- Astro no longer copies SDK skills into the project
- Astro does not install the Main Sequence SDK or expose its CLI
- existing session capability materialization remains unchanged

## Decisions

1. Astro uses Django's existing `/mcp` endpoint.
2. MCP authentication uses the same runtime bearer token Astro already uses
   for Django API requests.
3. MCP does not have separate credentials, token exchange, or authentication
   settings.
4. The MCP URL is always derived from `MAINSEQUENCE_BACKEND`:

   ```text
   {MAINSEQUENCE_BACKEND}/mcp
   ```

5. MCP is automatically enabled for every durable Tau `CodingSession`.
6. There is no MCP feature flag.
7. CodeRepository-owned `.agents` content is read directly from the project.
8. Platform MCP resources remain MCP resources. They are not copied or
   converted into local Tau skill files.
9. `AgentCapability`, its bindings, APIs, and Astro materialization remain
   unchanged.
10. SDK skill copying through `ensure_sdk_skills` is removed.
11. This change does not add Pi- or Tau-specific backend endpoints.
12. The `mainsequence` package, its CLI-auth tool, and SDK-version image tag are
    not part of the Astro runtime.
13. The official `mcp` package remains an Astro runtime dependency.

## Behavior to Remove

Remove:

```text
ensure_sdk_skills(cwd)
mainsequence Python dependency
ensure_mainsequence_cli_auth Tau tool
MAINSEQUENCE_CLI_AUTH_REPAIR_* settings
astro/astro-tau:ms-sdk-<version> image tag
```

This function copies Main Sequence SDK skills into
`<project>/.agents/skills/mainsequence`. That copy is obsolete because:

- CodeRepository-specific skills already belong in the CodeRepository
- general Main Sequence platform skills are MCP resources

Keep:

```text
materialize_session_capabilities(...)
```

Backend `AgentCapability` bindings are a separate feature. Astro must continue
materializing those session capabilities exactly as it does now.

## Authentication

Astro already authenticates through:

```text
src/astro/backend/auth.py
```

The existing flow is:

```text
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET
  -> POST /orm/api/pods/runtime-credentials/token/
  -> short-lived knative_runtime bearer token
  -> MainSequenceClient Django requests
```

The MCP client must use the same `RuntimeCredentialAuth` instance and the same
bearer token. Do not create another token type or endpoint.

Django MCP currently rejects access-only runtime scopes. Change MCP
authentication so it accepts `knative_runtime` only when:

- `runtime_target_kind` is `coding_agent_service`
- the referenced coding-agent service still exists and is valid
- the token resolves to its existing principal user and organization

Continue rejecting:

- `job_run_runtime`
- organization test runtime credentials
- resource-release runtime credentials
- invalid or missing coding-agent service targets

MCP tool calls continue through the existing MCP-to-DRF adapter. Existing DRF
permissions remain responsible for operation and object authorization.

## Minimal Astro Structure

Add only:

```text
src/astro/backend/mcp.py
src/astro/tools/mainsequence_mcp.py
```

Do not create a separate `src/astro/mcp/` package.

### `src/astro/backend/mcp.py`

This module owns:

- deriving the MCP URL from `Settings.backend_url`
- opening the official Python MCP Streamable HTTP client
- obtaining bearer headers from the existing `RuntimeCredentialAuth`
- initializing the MCP session
- listing tools and resources
- calling a tool
- reading a resource
- closing the MCP connection

The official MCP SDK is an Astro base-image dependency:

```text
mcp>=1.27,<2
```

This dependency is the official MCP protocol client. It does not require or
replace the removed `mainsequence` package.

Add it to Astro's root dependency and lock files so the existing image build:

- downloads its locked wheels into `/opt/wheels`
- installs it into `astro-runtime`
- includes it in `code-repository-executor-bundle`
- installs it into the active Python 3.13 environment produced by
  `Dockerfile.remote-worker`

Do not add `mcp` to individual project dependency files and do not install it
at container startup.

### `src/astro/tools/mainsequence_mcp.py`

This module owns:

- converting each Django MCP tool into a Tau `AgentTool`
- calling the canonical MCP tool name
- converting MCP text and structured results into `AgentToolResult`
- exposing one Tau tool for reading an advertised MCP resource
- generating a short resource index for the system prompt

Keep name conversion, schema handling, and result conversion as private
functions in this module. Split them only if the implementation becomes too
large to maintain.

MCP tool names containing dots must be converted deterministically:

```text
project.list -> mainsequence__project_list
```

If two MCP names produce the same Tau name, fail session loading instead of
inventing another naming scheme.

## Astro Runtime Changes

Update `SessionRuntimeManager._load`:

```text
load backend session
  -> acquire runtime lease
  -> resolve provider and code repository cwd
  -> materialize backend session capabilities
  -> open Django MCP using existing Astro authentication
  -> list MCP tools and resources
  -> add MCP tools to the Tau tool list
  -> add the MCP resource reader tool
  -> include the short resource index in the system prompt
  -> load CodingSession
```

MCP is required. If MCP initialization fails, durable session loading fails
instead of silently running without Main Sequence platform access.

Store the MCP client on `ActiveSessionRuntime` and close it when:

- session loading fails
- the runtime is evicted
- the runtime loses its lease
- Astro shuts down

Keep the generated capability root in Tau resource paths:

```python
TauResourcePaths(
    root=resource_root(),
    cwd=cwd,
    agents_root=session_agents_root,
)
```

This preserves both:

- project-owned `.agents` content discovered from `cwd`
- backend-bound session capabilities discovered from `session_agents_root`

## Remove From Astro

Remove:

```text
src/astro/resources/loader.py
  ensure_sdk_skills
  _resolve_skill_source

src/astro/runtime/manager.py
  ensure_sdk_skills(...)

src/astro/tools/mainsequence_auth.py

src/astro/resources/APPEND_SYSTEM.md
  Main Sequence CLI and SDK-skill instructions

pyproject.toml
  mainsequence dependency

deployment/gcp/cloudbuild.yaml
  ms-sdk-<version> tag and push
```

Remove these environment variables everywhere:

```text
MAINSEQUENCE_DISABLE_SKILL_DISCOVERY
MAINSEQUENCE_SKILL_COPY_BIN
MAINSEQUENCE_CLI_AUTH_REPAIR_COMMAND
MAINSEQUENCE_CLI_AUTH_REPAIR_TIMEOUT_SECONDS
```

Do not add MCP-specific environment variables.

Keep:

```text
ASTRO_SESSION_ASSET_ROOT
ASTRO_A2A_ASSET_ROOT
src/astro/capabilities/
tests/unit/test_capability_materializer.py
```

## Preserve Capability Contracts

Keep these Astro client methods:

```text
list_session_capabilities
get_capability_content
```

Keep these Astro backend models:

```text
AgentCapability
SessionCapabilityBinding
CapabilityContent
```

Keep the Django capability models, choices, bindings, serializers, views,
routes, services, admin registrations, OpenAPI definitions, and tests.

In particular, keep:

```text
AgentCapabilityKind
AgentCapabilitySourceType
AgentCapability
AgentCapabilityBinding
AgentSessionCapabilityBinding
AgentCardCapabilitySyncService
```

Keep all existing capability endpoints. They are independent control-plane
contracts and Astro session startup continues materializing their bound
content.

## Existing Django MCP Content

Astro must consume the existing Django MCP gateway:

```text
tdag-django/timeseries_orm/mcp_gateway/
```

Its current platform resources include:

```text
mainsequence://platform/ontology
mainsequence://platform/skills/a2a-communication
mainsequence://platform/skills/code-repository-design
mainsequence://platform/skills/code-repository-to-agent
mainsequence://platform/skills/static-site
```

Do not copy these resources into Astro or the project.

## Tests

### Astro

Test that:

- MCP uses the same authorization provider as `MainSequenceClient`
- the MCP URL is derived from `MAINSEQUENCE_BACKEND`
- every MCP tool is exposed as a Tau tool
- MCP tool names are converted deterministically
- MCP tool schemas are preserved
- MCP text and structured results are returned to Tau
- advertised MCP resources can be read
- project `.agents` skills remain discoverable
- SDK skills are not copied into the project
- the Main Sequence CLI-auth tool is not exposed to Tau
- backend session capabilities are still materialized and discovered
- MCP is closed on load failure, eviction, lease loss, and shutdown
- session loading fails when required MCP initialization fails

### Django

Test that:

- a valid coding-agent-service `knative_runtime` token can call `/mcp`
- the MCP request resolves to the runtime credential's principal user
- normal MCP DRF permissions still apply
- job-run, organization-test, and resource-release runtime tokens are rejected
- invalid or deleted coding-agent service targets are rejected
- existing interactive MCP authentication continues to work

### Container

Test the supported topology:

```text
Astro container
  -> MAINSEQUENCE_BACKEND=http://host.docker.internal:8000
  -> http://host.docker.internal:8000/mcp
```

No additional MCP configuration is required.

Verify the built Astro and remote-worker images can import `mcp` before
deployment. The Astro base image must not install `mainsequence`. Code repositories must
not declare or install the MCP SDK themselves.

## Delivery Order

1. Update Django `/mcp` authentication to accept valid coding-agent-service
   runtime tokens.
2. Add the minimal Astro MCP adapter and remove `ensure_sdk_skills`.
3. Remove the Main Sequence SDK dependency, CLI-auth tool, CLI prompt policy,
   and SDK-version image tag.
4. Deploy Django, then Astro.
5. Confirm Astro no longer copies SDK skills into the project.
6. Confirm the existing capability API and client contract tests still pass.
7. Confirm backend session capabilities are still materialized.

## Definition of Done

- Astro uses one authentication mechanism for Django API and MCP traffic.
- MCP requires no additional credentials or environment variables.
- Every durable Tau session automatically includes Main Sequence MCP.
- CodeRepository-owned resources are read from the project repository.
- Platform knowledge and operations come only from Django MCP.
- Astro does not copy SDK platform skills into the project.
- Astro does not install the `mainsequence` package or expose its CLI.
- Astro images are not tagged by Main Sequence SDK version.
- Astro continues materializing backend-bound session capabilities.
- Astro capability client methods and models remain available.
- Django capability models and endpoints remain unchanged.
- Existing A2A and unrelated capability contracts remain unchanged.
