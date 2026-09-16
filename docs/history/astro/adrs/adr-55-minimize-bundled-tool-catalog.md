# ADR 55: Minimize Astro's Bundled Tool Catalog

Status: Accepted

Date: 2026-09-16

Implementation Status: Implemented in source; deployment rollout pending

Supersedes:

- the `tau-file-tools`, `tau-web-access`, and `get_runtime_info` requirements in
  `docs/implemenation_task/remove-node-pi-migrate-to-tau.md`;
- the migration requirement that Astro preserve Pi's optional `grep`, `find`, `ls`, and web-access
  tool surfaces;
- the migration requirement that Astro retain a model-facing runtime-diagnostics tool; and
- the migration deletion-map decisions to replace Pi capabilities with bundled
  `tau-file-tools` and `tau-web-access` dependencies.

Amends:

- ADR 52 by removing web tools, structured file tools, and `runtime_info` from Astro's base
  durable-session tool composition; and
- ADR 50 by making optional model tools and their Python dependencies project-owned rather than
  part of the lean runtime ABI. The `ripgrep` executable may remain available to Tau's core
  `bash` tool as part of the CodeRepository execution environment.

## Context

Commit `781ecf4` migrated Astro from Pi to Tau. The migration specification tried to preserve the
existing Pi-facing tool surface while removing Node.js. It introduced three Astro-owned
capability groups beyond Tau's core coding tools:

1. the standalone `tau-file-tools` distribution providing structured `grep`, `find`, and `ls`;
2. the source-preserving `tau-web-access` port; and
3. the model-facing `runtime_info` diagnostics tool.

The migration document itself records that Pi's and Tau's default full-access coding set was only
`read`, `write`, `edit`, and `bash`. Pi exported `grep`, `find`, and `ls` through optional
read-only/all-tools sets. Packaging them as `tau-file-tools` made migration easier; it did not make
them Astro platform responsibilities.

The web-compatibility requirement included:

- `web_search`;
- `code_search`;
- `fetch_content`;
- `get_search_content`;
- search-provider fallback behavior;
- GitHub cloning;
- HTML and PDF extraction; and
- YouTube transcript and video handling.

The resulting `packages/tau-file-tools` and `packages/tau-web-access` distributions, their Astro
composition code, and `runtime_info` were compatibility work for that migration. They are not
required by Tau, FastAPI, assistant-ui chat, A2A, Main Sequence MCP, runtime-credential
authentication, backend persistence, provider communication, repository execution, or Tau's core
coding tools.

Bundling those tools in Astro currently has five undesirable effects:

1. Every durable `CodingSession` receives seven optional file/web tools and one diagnostics tool
   whether the project wants them or not.
2. Structured `grep`, `find`, and `ls` duplicate operations already available through Tau's core
   `bash` tool and common CodeRepository command-line utilities.
3. `runtime_info` puts deployment and process diagnostics into the model tool catalog even though
   they belong in health, version, logging, and operator-facing diagnostics.
4. The base runtime owns a dependency graph for HTML conversion, YouTube handling, general HTTP
   extraction, and media download behavior that is unrelated to its platform responsibilities.
5. Astro becomes responsible for the behavior, security posture, compatibility, image contents,
   and release churn of optional agent capabilities.

ADR 52 now provides the correct extensibility boundary. A trusted CodeRepository may put Python
extensions under `<repository>/.tau/extensions`, and those extensions can register arbitrary Tau
`AgentTool` instances. The CodeRepository image already owns its project dependencies and shares
the final `/opt/venv` Python environment with Astro.

Structured convenience tools and web access are therefore examples of project extensions, not
platform primitives. Runtime diagnostics are an operator concern, not a model capability.

## Decision

### 1. Remove migration-era optional tools from Astro completely

Astro will no longer build, install, import, register, verify, test, document, or publish
`tau-file-tools` or `tau-web-access`. Astro will also remove the model-facing `runtime_info` tool.

The removal includes:

- `packages/tau-file-tools` and all of its tests;
- `packages/tau-web-access` and all of its tests;
- `src/astro/tools/runtime_info.py` and its system-prompt guidance;
- `src/astro/tools/web_access.py`;
- `tau-file-tools` and `tau-web-access` from Astro's runtime dependencies and workspace wiring;
- automatic registration of `grep`, `find`, `ls`, and `runtime_info`;
- the manager-owned web HTTP client and per-session web result store;
- automatic registration of `web_search`, `code_search`, `fetch_content`, and
  `get_search_content`;
- Docker build, Compose mount/PYTHONPATH, offline wheelhouse, import, and package checks that exist
  only for either bundled package;
- web-provider environment guidance owned only by the removed package; and
- Astro coverage, type-check, documentation, and release assertions for the removed packages and
  diagnostics tool.

The `ripgrep` executable is not a model-facing `AgentTool` and is not removed by this decision. It
may remain part of the CodeRepository execution environment for repository work through Tau's
core `bash` tool. Removing or retaining general command-line utilities is an image-ABI decision,
not an extension-ownership decision.

This is a hard removal. Astro will not retain:

- a feature flag for the old built-in tools;
- an optional `mainsequence-astro[web]` extra;
- a compatibility shim that imports a project package when present;
- Astro-managed file-tool or web-tool extension templates;
- dynamic dependency installation;
- fallback file or web implementations;
- a renamed or reduced `runtime_info` model tool; or
- a guaranteed replacement for any removed Pi-compatible tool contract.

The historical packages may continue to exist in Git history. They are not retained in the active
repository or release artifacts.

### 2. Keep HTTP and chat transport unchanged

The removed tools are model-facing optional tools. The web subset is unrelated to Astro's HTTP
transport.

Astro retains `httpx` and every HTTP path required for:

- FastAPI/Uvicorn request handling;
- `POST /api/chat` and assistant-ui SSE streaming;
- A2A REST, JSON-RPC, and streaming;
- runtime-credential exchange;
- Django REST and MCP communication;
- session persistence, leases, and task control;
- provider API communication; and
- runtime health and readiness.

No public HTTP route, request format, response format, authentication flow, or backend protocol is
changed by this decision. Only the default model tool catalog changes.

### 3. Make optional tools entirely project-owned

A project that wants structured file discovery, web search, YouTube access, a browser, database
access, runtime introspection, or any other optional model tool must provide its own trusted Tau
extension.

The project owns:

- selecting or implementing the tool package;
- declaring and pinning dependencies in its CodeRepository image build;
- placing the extension entry point under `.tau/extensions`;
- registering `AgentTool` objects through Tau's public `ExtensionAPI`;
- API keys, secrets, provider accounts, egress rules, and external-service terms;
- tool schemas, results, timeouts, retries, cancellation, security controls, and compatibility;
- resource ownership and cleanup inside the extension; and
- tests and release management for that extension.

Astro does not install project dependencies at session load or process startup. A missing project
dependency is an extension import error surfaced through Tau's existing diagnostics; it does not
cause Astro to fetch or repair the package.

Astro provides no compatibility promise for the names or behavior of project-owned tools. A
project may recreate any former name, choose a different package, expose a smaller surface, or
provide none of those tools.

### 4. Retain only the extension-host contract

Astro remains responsible for the host behavior required by arbitrary trusted project extensions:

- enabling project-extension discovery in CodeRepository Executor deployments;
- using `/workspace` as the configured Tau working directory;
- preserving the `/workspace/src:/workspace` import ABI in the final executor;
- composing registered extension tools and hooks into durable `CodingSession` instances;
- reporting extension load/runtime diagnostics and the effective tool-catalog digest through
  operator-facing logs and health/diagnostic state rather than a model-facing tool;
- preventing request payloads or prompts from enabling extensions dynamically; and
- closing each `CodingSession` during eviction and process shutdown so Tau can emit its documented
  `session_shutdown` lifecycle event.

The last item is a host correctness requirement, not ownership of extension cleanup. An extension
is responsible for reacting correctly to the lifecycle event; Astro is responsible for delivering
the event by calling the public `CodingSession.aclose()` path.

Extension code remains trusted repository code running with the same process identity, filesystem,
environment, and network access as the rest of the CodeRepository deployment. This ADR introduces
no new sandbox, permission system, package installer, registry, or MCP bridge.

### 5. Preserve the existing execution-surface boundary

Repository extensions remain available only on durable execution surfaces backed by
`SessionRuntimeManager`:

- assistant-ui chat;
- direct session-backed A2A messages; and
- backend-backed A2A Task execution using the same loaded session runtime.

The agent-targeted sessionless response surface continues to construct a tool-free
`AgentHarness`. Removing bundled web access does not add project-extension loading to that path.

## Effective Tool Composition

After this decision, the Astro-owned durable-session composition is:

```text
Tau core coding tools: read, write, edit, bash
+ Main Sequence MCP tools
+ A2A task-control tools required by the execution protocol
+ trusted repository .tau/extensions tools and hooks
```

No Astro-owned `grep`, `find`, `ls`, `runtime_info`, web-search, or content-fetch tool is present.
Any such tool appears only when a repository extension registers it:

```text
CodeRepository image build
  -> project installs its chosen dependencies into /opt/venv
  -> project supplies /workspace/.tau/extensions/<name>/extension.py
  -> Tau imports the trusted extension when a CodingSession cold-loads
  -> setup(tau) registers project-owned AgentTools and lifecycle hooks
  -> Tau composes them with the Astro-owned base tools
```

There is no runtime installation or Astro-specific discovery protocol.

## Consequences

### Positive

- The base image, wheelhouse, and model tool catalog become smaller and more stable.
- Astro no longer owns optional structured file-search, runtime-introspection, search, scraping,
  YouTube, video, or media-downloader behavior.
- Projects expose only the optional capabilities they intentionally select.
- Provider keys and egress policy can be scoped to deployments that need them.
- Dependency and supply-chain risk moves to the project that selected the capability.
- The architecture exercises Tau's native extension boundary instead of retaining Pi migration
  baggage as a platform feature.
- Models receive fewer irrelevant schemas on every turn.

### Negative

- Existing projects that implicitly relied on any of the eight removed tools lose them after
  deployment of the new image.
- There is no automatic compatibility extension or tool-name preservation.
- A project that wants structured `grep`/`find`/`ls`, web access, or model-facing runtime
  introspection must build and maintain that capability explicitly.
- Models must use Tau's core `bash` tool for command-line `rg`, `find`, and `ls` operations when a
  project has not supplied structured equivalents.
- Runtime and project-extension diagnostics must be inspected through operator-facing surfaces
  rather than by asking the model to call `runtime_info`.
- Tool catalogs can differ between projects and repository revisions, which is intentional and
  must remain visible in runtime diagnostics.

## Compatibility And Rollout

This is an intentional tool-catalog breaking change with no compatibility mode.

Before deploying the removal:

1. identify projects that intentionally require any removed structured file, runtime-info, or
   web-access tool;
2. require each such project to add and verify its own dependency and `.tau/extensions` entry;
3. verify the CodeRepository deployment preserves the extension flag and import ABI;
4. deploy the new Astro image; and
5. cold-load representative chat and A2A sessions and inspect extension diagnostics/tool catalogs.

Projects that do not explicitly add extensions receive none of the removed tools. Tau's core
`read`, `write`, `edit`, and `bash` remain available. Warm in-process sessions from an older image
disappear with the normal deployment replacement; newly loaded sessions use the new catalog.

Rollback is an image rollback. No database migration or persisted-session transformation is
required because Tau entries do not require the removed tool definitions merely to be read. A
replayed model turn must not be attempted across two different tool catalogs without the normal
runtime/deployment compatibility checks.

## Verification

The implementation is complete when all of the following are true:

- importing `astro` does not import or require `tau_file_tools` or `tau_web_access`;
- the root package has no dependency or optional extra for either removed package;
- release images and the offline wheelhouse contain neither package nor dependencies used only by
  them;
- the default durable-session tool catalog omits `grep`, `find`, `ls`, `runtime_info`,
  `web_search`, `code_search`, `fetch_content`, and `get_search_content`;
- chat, A2A, backend, MCP, provider, persistence, lease, and health tests continue to pass;
- a fixture CodeRepository can install its own dependency and register representative optional
  tools through `.tau/extensions` without Astro-specific code;
- an extension `session_shutdown` hook runs during normal runtime eviction;
- sessionless responses remain tool-free;
- documentation no longer presents the removed tools as Astro-provided features; and
- repository search finds no active runtime, image, or documentation dependency on the removed
  packages or tool outside historical decision material that is explicitly marked superseded.

## Rejected Alternatives

### Keep the package but stop registering its tools

Rejected for both standalone packages. Their code and dependency graphs would remain in the base
image and release process even though Astro no longer owns the capabilities.

### Keep optional Astro extras

Rejected. They would preserve Astro ownership of package selection, compatibility, and release
support. Project dependencies already provide the correct opt-in mechanism.

### Keep `tau-file-tools` as harmless coding conveniences

Rejected. Pi did not include `grep`, `find`, and `ls` in its default full-access coding set, Tau
already exposes `bash`, and projects can register structured equivalents when their workflows need
them. Their small implementation size does not justify making Astro own another tool package and
contract.

### Wait for Tau to upstream the structured file tools

Rejected as a prerequisite for removal. If Tau later includes these tools in its own core contract,
Astro can consume that Tau release without reviving the standalone Astro-owned package. Until then,
`bash` or a project extension is sufficient.

### Retain `runtime_info` for debugging

Rejected. Provider/model/session, Python, release, path, and extension-catalog information is
operational metadata. Astro will expose and test necessary diagnostics through operator-facing
health, version, and logging surfaces, not spend model context and tool calls on a diagnostic
capability.

### Publish an official Astro web extension

Rejected as part of this decision. Users are responsible for the extensions they install. A
separately owned project may publish a web extension independently, but it is not an Astro artifact
or compatibility obligation.

### Move the old tools into Main Sequence MCP

Rejected. Generic web browsing is not a Main Sequence control-plane responsibility, and moving it
to MCP would replace one incorrect ownership boundary with another.

### Retain only YouTube transcript support

Rejected. Ordinary YouTube transcript fetching is still an optional project capability. Neither
transcript fetching nor broader video extraction belongs in the Astro base runtime.

## Implementation Plan

The executable removal plan is maintained in
`docs/implemenation_task/minimize-bundled-tool-catalog.md`.
