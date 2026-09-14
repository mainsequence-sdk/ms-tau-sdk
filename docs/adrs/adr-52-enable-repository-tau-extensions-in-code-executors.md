# ADR 52: Enable Repository Tau Extensions in CodeRepository Executors

Status: Accepted

Date: 2026-09-14

Implementation Status: Partially implemented — Astro runtime and executor-image work is complete;
the tdag-django deployment override remains pending.

Owners: Astro Tau, tdag-django CodeRepository Executor deployment, and Runtime Infrastructure

Related decisions:

- `docs/adrs/adr-50-lean-python-runtime-abi.md`
- `docs/reference/adr-32-agent-session-capability-bindings.md`
- `docs/reference/adr-39-unified-pi-runtime-context.md`
- `docs/implemenation_task/remove-node-pi-migrate-to-tau.md`
- [`tdag-django ADR-047: Unified Lean Python Runtime ABI And Destructive Image Cutover`](../../../tdag-django/docs/tdag/pod_manager/adr/adr-047-unified-lean-python-runtime-abi-cutover.md)

## Context

Astro constructs each durable Tau `CodingSession` with a fixed tool list:

- Tau coding tools;
- `tau-file-tools`;
- `tau-web-access`;
- tools and resources discovered from the Main Sequence Django MCP server; and
- Astro runtime information.

The CodeRepository checkout is supplied as the Tau working directory, so project-local skills and
prompt resources are discovered. Executable project extensions are different. Astro currently
passes `project_extensions_enabled=False` for every durable session, including sessions running in
a CodeRepository Executor deployment.

Tau already has a native project extension contract. When enabled, it discovers Python extensions
under `<cwd>/.tau/extensions`. An extension receives Tau's `ExtensionAPI` and may register an
`AgentTool`, lifecycle hooks, prompt guidance, commands, and renderers. Registered tools are
composed into the harness tool list and become structured model tools.

Disabling this in a CodeRepository Executor is the wrong boundary. The executor image is built from
the CodeRepository, uses the repository's Python environment, exposes its files through coding and
shell tools, and exists specifically to execute that project's Python code. A repository extension
is another project entry point in the same deployment; it is not a new category of executable
content or a separate security boundary.

The current setting creates a product gap:

- a repository can provide skills explaining a CLI but cannot expose a first-class structured Tau
  tool;
- project operations appear only as generic `bash` calls rather than named, schema-validated tool
  calls;
- project tools cannot use Tau-native cancellation, progress updates, execution modes, hook
  integration, or structured results; and
- the deployed repository's executable agent surface differs from the same repository running in
  the Tau CLI with project extensions enabled.

Main Sequence MCP discovery does not close this gap. Django MCP exposes platform tools from a
different process. Making repository Python available through MCP would require a separate MCP
server or bridge and is unnecessary for tools that belong to the deployed Tau project coder.

## Decision

### 1. CodeRepository Executor deployments enable repository extensions

CodeRepository Executor deployments will enable Tau project extension discovery.

Astro adds the runtime setting:

```text
ASTRO_CODE_REPOSITORY_EXTENSIONS_ENABLED=true|false
```

The canonical CodeRepository Executor image recipe sets it to `true`. Astro passes the resolved
value into `CodingSessionConfig.project_extensions_enabled`.

The setting exists because the same Astro package can also run without a CodeRepository-attached
deployment. It describes runtime composition; it is not a user permission, capability grant, or
model-selected option. Chat and A2A request bodies do not control it.

The resulting composition is:

```text
CodeRepository Executor image
  -> /workspace is the deployed project
  -> ASTRO_CODE_REPOSITORY_EXTENSIONS_ENABLED=true
  -> Tau discovers /workspace/.tau/extensions
  -> extensions register project AgentTools and hooks
  -> project tools join the durable session harness
```

### 2. Use Tau's native extension contract

Astro will not create a second project-tool plugin format. Discovery follows the pinned Tau
version's native conventions under `/workspace/.tau/extensions`, including:

- a Python extension file;
- a directory containing `extension.py`; or
- paths declared by a directory's `pyproject.toml` under `[tool.tau]`.

Extensions register tools through `ExtensionAPI.register_tool`. They may import the repository and
its dependencies from the CodeRepository image's existing Python environment. Astro does not
install dependencies while loading a session and does not create another interpreter or virtual
environment.

Tau remains responsible for extension discovery, registration order, tool composition, lifecycle
hooks, reload behavior, and diagnostics. Astro must not fork those semantics into a parallel
extension implementation. Tool-name precedence follows the pinned Tau extension contract and must
be covered by compatibility tests so a Tau upgrade cannot change it unnoticed.

This decision adds no project MCP server, dynamic package installer, remote extension registry, or
backend materialization of executable Python.

### 3. Canonical project layout and Python import contract

For a CodeRepository Executor, the canonical extension location is relative to the configured Tau
working directory:

```text
ASTRO_CODE_REPOSITORY_CWD=/workspace
<cwd>/.tau/extensions=/workspace/.tau/extensions
```

A typical project layout is:

```text
/workspace/
├── .tau/
│   └── extensions/
│       └── portfolio_tools/
│           ├── extension.py
│           └── formatting.py
├── pyproject.toml
└── src/
    └── project_coder/
        ├── __init__.py
        └── portfolios.py
```

Tau loads a directory containing `extension.py` as a package. Relative imports within the extension
directory therefore use normal package-relative syntax:

```python
from .formatting import render_summary
```

Imports from the CodeRepository's application source are separate. Loading an extension by file
path does not, by itself, make `/workspace/src` importable. Running Astro with `WORKDIR /workspace`
also does not provide a sufficient console-script import contract.

The CodeRepository Executor Python ABI is therefore amended to guarantee this effective import
path before Astro starts:

```text
PYTHONPATH=/workspace/src:/workspace
```

The non-existent `/workspace/src` entry is harmless for a flat-layout repository. The ordering
allows a standard `src` layout to resolve the project package while retaining support for flat
modules rooted directly under `/workspace`.

The final executor continues to use `/opt/venv/bin/python`. Declared third-party dependencies are
installed into `/opt/venv` by the CodeRepository image build. A project may additionally install
itself into that environment, including through an editable installation when live source edits
must be reflected, but extension imports must not depend on every repository being packaged and
installed. The canonical `PYTHONPATH` makes both supported source layouts deterministic.

Project extensions must import application modules by their package name:

```python
from project_coder.portfolios import calculate_portfolio
```

They must not add hard-coded repository paths to `sys.path`. Import-path ownership belongs to the
executor deployment contract rather than to every extension.

The cross-project ownership is:

- Astro's `Dockerfile.remote-worker` declares the CodeRepository extension flag and canonical
  `PYTHONPATH` in the final executor image;
- tdag-django's `run_knative_service_from_job` deployment path preserves that ABI and must not
  replace it with `/workspace` alone; at the time of this decision it unconditionally publishes
  `PYTHONPATH=/workspace`, which is the concrete cross-project gap;
- the CodeRepository image builder installs declared third-party dependencies into `/opt/venv`;
  and
- Astro/Tau performs extension discovery and registration after the final import environment is
  active.

The image and deployment verification paths must execute the final `/opt/venv/bin/python`, inspect
the effective `sys.path`, and prove imports from both a fixture extension sibling and a fixture
`/workspace/src` package.

#### Complete project tool example

Given this project function:

```python
# /workspace/src/project_coder/portfolios.py

def calculate_portfolio(portfolio_uid: str) -> dict[str, object]:
    return {
        "portfolio_uid": portfolio_uid,
        "asset_count": 3,
        "summary": f"Calculated portfolio {portfolio_uid}",
    }
```

The project registers it as a structured Tau tool with:

```python
# /workspace/.tau/extensions/portfolio_tools/extension.py

from __future__ import annotations

from collections.abc import Mapping

from tau_agent.messages import TextContent
from tau_agent.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolUpdateCallback,
)
from tau_agent.types import JSONValue

from project_coder.portfolios import calculate_portfolio

from .formatting import render_summary


async def execute_calculate_portfolio(
    tool_call_id: str,
    arguments: Mapping[str, JSONValue],
    signal: ToolCancellationToken | None = None,
    on_update: ToolUpdateCallback | None = None,
) -> AgentToolResult:
    del tool_call_id, on_update
    if signal is not None and signal.is_cancelled():
        return AgentToolResult(
            content=[TextContent(text="Portfolio calculation was cancelled.")],
            details={"cancelled": True},
        )

    result = calculate_portfolio(str(arguments["portfolio_uid"]))
    return AgentToolResult(
        content=[TextContent(text=render_summary(result))],
        details={
            "portfolio_uid": str(result["portfolio_uid"]),
            "asset_count": int(result["asset_count"]),
        },
    )


def setup(tau) -> None:
    tau.register_tool(
        AgentTool(
            name="calculate_portfolio",
            label="Calculate Portfolio",
            description="Calculate one portfolio using the deployed project implementation.",
            parameters={
                "type": "object",
                "properties": {
                    "portfolio_uid": {
                        "type": "string",
                        "description": "Portfolio UID to calculate.",
                    }
                },
                "required": ["portfolio_uid"],
                "additionalProperties": False,
            },
            execute_fn=execute_calculate_portfolio,
            execution_mode="sequential",
        )
    )
```

The sibling formatter is an ordinary relative import:

```python
# /workspace/.tau/extensions/portfolio_tools/formatting.py

from collections.abc import Mapping


def render_summary(result: Mapping[str, object]) -> str:
    return str(result["summary"])
```

Tau calls the synchronous `setup(tau)` entry point while loading the extension. The registered
tool executor may be asynchronous, receives Tau cancellation/update hooks, and returns a structured
`AgentToolResult`.

### 4. Compose project tools with the existing harness tools

The effective durable-session tool set becomes:

```text
Tau coding tools
+ file tools
+ web tools
+ Main Sequence MCP tools
+ Astro runtime tools
+ repository Tau extension tools
```

Extensions may also contribute prompt guidance and tool hooks according to Tau's native extension
API. Astro supplies its existing base tool list to `CodingSession`; Tau performs the final
composition.

Project tool names, descriptions, input schemas, and results are therefore visible to the model as
real `AgentTool` definitions. A project tool call is persisted and observed under its semantic tool
name instead of being indistinguishable from generic shell execution.

### 5. Load and diagnostics lifecycle

Project extensions load whenever Astro cold-loads a durable Tau `CodingSession`. Their runtime
state belongs to that loaded session and is discarded when the session is evicted or the process
exits.

Astro surfaces Tau's extension diagnostics through structured logs. The log contract includes the
extension name, repository-relative path, diagnostic severity, and error type, but never source
contents, tool arguments/results, credentials, or environment values.

The runtime health payload and `runtime_info` diagnostics expose the project-extension enablement
state, loaded extension count, registered project-tool count, extension diagnostic and error
counts, and a deterministic tool-catalog digest. This distinguishes a deployment where no
extensions were present from one where discovery or loading failed.

The CodeRepository image verification suite includes a fixture extension that imports from the
project environment and registers a tool. Extension errors follow Tau's native loading semantics;
Astro reports them rather than inventing a second error policy.

### 6. Execution surfaces

This decision applies to durable execution through `SessionRuntimeManager`:

- Assistant UI chat;
- direct A2A `message:send` using a backend-owned `AgentSession`; and
- backend-backed A2A task execution using that same session runtime.

All three surfaces share the same loaded `CodingSession` and therefore receive the same project
tools and hooks.

This decision does not enable project extensions for the agent-targeted sessionless
`/api/agents/{agent_uid}/responses` endpoint. That endpoint constructs a one-shot `AgentHarness`
instead of a CodeRepository `CodingSession`. Its sessionless-capability policy remains a separate
decision.

The LLM-only endpoint also remains tool-free.

### 7. Skills, CLIs, and extensions remain complementary

Enabling repository extensions does not require every project CLI command to become an
`AgentTool`.

- Skills describe workflows, selection criteria, and how to use broad or infrequent CLI surfaces.
- CLIs remain useful to humans and any harness with shell access.
- Tau extension tools provide structured arguments/results, semantic observability, native
  progress and cancellation, execution modes, and integration with Tau hooks.

Projects should keep domain behavior in reusable Python services where practical. A CLI entry
point and a Tau `AgentTool` adapter may call the same implementation instead of duplicating
business logic.

## Runtime Boundary

Repository extensions run with the same CodeRepository container identity, Python environment,
working directory, filesystem access, and process environment as the rest of the project-coder
runtime. This is intentional. The CodeRepository deployment, not Astro's extension loader, defines
the executable-code boundary.

Project extensions do not receive a new Main Sequence authorization identity. Calls made through
existing Astro-provided Main Sequence tools continue to use Astro's authenticated session context
and caller-session proof. An extension may implement project behavior directly or register a tool
that composes existing APIs, but this decision does not change backend authorization.

## Alternatives Considered

### Keep project extensions disabled and use only CLI plus skills

Rejected as the only supported model. It remains a valid project design, but Tau sees every
operation as generic shell execution and loses schemas, semantic telemetry, native progress, and
structured errors.

### Expose every repository tool through Django MCP

Rejected for project-local tools. Django cannot execute code inside the separate CodeRepository
Executor process without a new callback or server architecture. MCP remains appropriate for tools
intentionally provided by an MCP service to multiple harnesses, but it is not required for a tool
owned by one Tau deployment.

### Create an Astro-specific project-tool manifest and loader

Rejected. Tau already defines discovery, registration, composition, hooks, commands, reload, and
diagnostics. A second format would create incompatible project packages and duplicate runtime
behavior.

### Materialize backend `AgentCapability(kind="extension")` into Python

Rejected by this decision. Session capability materialization currently handles declarative skill
content. Repository extensions are already delivered by the CodeRepository image; executable
capability distribution from the backend is a different feature.

## Consequences

### Positive

- CodeRepository deployments can expose first-class domain tools to Tau.
- Tool discovery uses Tau's existing extension API instead of an Astro-specific plugin framework.
- Project tools can provide schemas, structured output, cancellation, updates, execution modes,
  hooks, and semantic observability.
- Project extensions can import the same project code and dependencies that the executor already
  runs.
- Skills and CLIs remain portable and can share implementation with Tau tool adapters.

### Negative

- Extension import and setup add work to cold session loading.
- Project authors must maintain compatibility with Astro's pinned Tau extension API.
- A broken project extension can produce load diagnostics or affect a session in the same way as
  broken project Python invoked through another project entry point.
- Tool catalogs can differ between CodeRepository revisions and must be observable during
  deployment and runtime debugging.

## Implementation Plan

- [x] Add `Settings.code_repository_extensions_enabled`, mapped from
      `ASTRO_CODE_REPOSITORY_EXTENSIONS_ENABLED`.
- [x] Set `ASTRO_CODE_REPOSITORY_EXTENSIONS_ENABLED=true` in the canonical
      `Dockerfile.remote-worker` CodeRepository Executor contract.
- [x] Set the final CodeRepository Executor `PYTHONPATH` to
      `/workspace/src:/workspace` in `Dockerfile.remote-worker`.
- [ ] Amend
      `tdag-django/timeseries_orm/tdag/pod_manager/services/runtime/deployments.py::run_knative_service_from_job`
      and its Knative contract tests to publish `/workspace/src:/workspace` rather than
      overwriting the final image contract with `/workspace` alone.
- [ ] Confirm
      `tdag-django/timeseries_orm/agents/services/shared.py::_build_code_repository_executor_runtime_env`
      preserves the canonical import path through CodeRepository Executor deployment assembly.
- [x] Pass the setting to `CodingSessionConfig.project_extensions_enabled`.
- [x] Emit Tau extension diagnostics through Astro's structured logging contract.
- [x] Add loaded extension count, project-tool count, extension diagnostic/error counts, and a
      tool-catalog digest to runtime diagnostics.
- [x] Add an image verification fixture that imports a repository extension and registers a tool.
- [x] Test that a durable chat session can invoke a project extension tool.
- [ ] Test that direct and task-based A2A sessions receive the same project extension tool set.
- [x] Test Tau-native tool precedence and hook behavior through Astro composition.
- [x] Test that a CodeRepository extension can import project dependencies from the shared Python
      environment.
- [x] Test package imports from `/workspace/src`, flat imports from `/workspace`, and relative
      sibling imports inside a directory extension.
- [x] Run the import checks with the final `/opt/venv/bin/python` console-script environment, not
      only with `python -c` from the repository root.
- [ ] Test that request bodies, prompts, and skills cannot change deployment extension enablement.
- [x] Document `.tau/extensions` authoring and the shared-implementation pattern for CLI and Tau
      adapters.

## Acceptance Criteria

The decision is implemented when an extension under `.tau/extensions` in a CodeRepository Executor
deployment is automatically loaded into every durable Chat and A2A Tau session, its registered
tools and hooks use Tau's native behavior, project dependencies are importable, extension state and
diagnostics are observable, no new MCP service or Astro-specific extension format is introduced,
`/workspace/src` and `/workspace` imports resolve under the final `/opt/venv` interpreter, and
sessionless/LLM-only endpoint behavior remains unchanged.
