# Minimize Astro's Bundled Tool Catalog

Status: Implemented in source; deployment rollout pending

Date: 2026-09-16

Decision: `docs/adrs/adr-55-minimize-bundled-tool-catalog.md`

Historical source: commit `781ecf4` (`Migrate Astro runtime from Pi to Tau`)

## Objective

Delete the Pi-compatibility file-tool and web-access packages and the model-facing `runtime_info`
tool from Astro and its release artifacts. Preserve Tau's core coding tools, all
HTTP/chat/backend communication, protocol-required platform tools, and the generic Tau
project-extension contract.

This is a hard removal. The final repository contains no active `tau-file-tools` or
`tau-web-access` package, Astro web adapter, `runtime_info` tool, built-in registration for the
eight removed tool names, optional Astro tool extra, or dynamic installation path. Projects that
want those capabilities own their extensions and all dependencies and configuration.

## Non-Goals

- Designing or publishing replacement file-tool or web-access packages.
- Maintaining compatibility with any of the eight former tool names or schemas.
- Migrating API keys into another Astro setting.
- Adding removed tools to Main Sequence MCP.
- Enabling tools on the sessionless response surface.
- Changing FastAPI, assistant-ui, A2A, backend, MCP, provider, or authentication transport.
- Installing project packages at runtime.
- Removing Tau's core `read`, `write`, `edit`, or `bash` tools.
- Removing `ripgrep` merely because the structured file-tool package is removed; command-line
  utilities remain a separate CodeRepository image-ABI decision.

## Ownership Contract

### Astro owns

- the thin Tau/FastAPI runtime;
- Main Sequence backend, MCP, persistence, lease, task, and provider communication;
- Tau's core coding-tool composition;
- CodeRepository project-extension discovery;
- the shared `/opt/venv` and `/workspace/src:/workspace` import ABI;
- extension composition and diagnostics; and
- delivery of Tau lifecycle events through `CodingSession.aclose()`.

### The project owns

- selecting, pinning, installing, testing, and updating extension dependencies;
- the `.tau/extensions` entry point;
- optional tool names, schemas, behavior, and compatibility;
- API credentials, external accounts, egress, and service terms;
- tool-level cancellation, timeouts, security, and output limits; and
- cleanup performed by its lifecycle handlers.

## Current Coupling To Remove

### Runtime

- `src/astro/runtime/manager.py` imports and registers `create_file_tools`, adding structured
  `grep`, `find`, and `ls` to every durable session.
- `src/astro/runtime/manager.py` imports `MemorySearchResultStore` and the Astro web adapter.
- `SessionRuntimeManager` creates a process-wide web `httpx.AsyncClient`.
- Every durable session creates a memory web-result store and registers four web tools.
- Manager shutdown closes the web-only client.
- `src/astro/tools/web_access.py` exists solely as an adapter to the compatibility package.
- `src/astro/tools/runtime_info.py` exposes process, provider, session, path, version, and extension
  diagnostics as a model-facing tool registered in every durable session.
- `src/astro/resources/APPEND_SYSTEM.md` instructs the model to call `runtime_info`.

### Packaging and images

- `mainsequence-astro` depends on `tau-file-tools` and `tau-web-access`.
- The root uv workspace, type-check, test, and coverage configuration include both packages.
- `requirements-runtime.lock` and `uv.lock` contain both packages and the web-only dependency
  graph.
- `Dockerfile` copies, builds, installs, and import-checks both packages.
- `Dockerfile.remote-worker` import-checks both packages.
- `docker-compose.yml` mounts both source trees and adds them to `PYTHONPATH`.
- `Dockerfile.dockerignore` retains their sources for the build context.
- runtime image verification requires both packages and historically required the web package's
  media executable.

### Tests and documentation

- `packages/tau-file-tools/tests` verifies an optional structured-tool surface Astro no longer
  owns.
- `packages/tau-web-access/tests` verifies a product surface Astro no longer owns.
- `tests/unit/test_runtime_tools.py` tests `runtime_info` and the Astro web adapter.
- runtime-manager tests patch the file/web adapters and assert `runtime_info` during session
  construction.
- CI includes both packages in formatting, typing, and coverage.
- README, system-prompt, environment, lifecycle, scope, folder-structure, quickstart, deployment,
  ADR, and migration documentation describe the removed tools as part of Astro.

## Work Plan

### Phase 1: Repair the generic extension lifecycle

This phase is a platform prerequisite for telling project authors that they own extension
resources.

- [ ] Update normal session eviction to call `runtime.coding_session.aclose()`.
- [ ] Ensure the call happens once and is bounded by the existing shutdown grace behavior.
- [ ] Continue closing the externally owned provider through the existing manager path unless Tau's
      public ownership contract proves that `CodingSession` owns it.
- [ ] Log extension/session close failures without skipping lease release, provider close, or
      registry removal.
- [ ] Add a test extension with a `session_shutdown` hook and prove that ordinary idle/manual
      eviction delivers `reason="quit"`.
- [ ] Prove manager process shutdown uses the same close path.
- [ ] Prove a failing extension shutdown hook cannot strand the runtime in the manager registry.

Acceptance:

- Project extensions receive the public Tau shutdown lifecycle on eviction.
- Astro provides the lifecycle event but contains no extension-specific cleanup code.

### Phase 2: Reduce the runtime tool catalog

- [ ] Remove the `tau_file_tools` import from `src/astro/runtime/manager.py`.
- [ ] Remove `create_file_tools(...)` from the durable-session base tool list.
- [ ] Delete `src/astro/tools/web_access.py`.
- [ ] Remove `tau_web_access` imports from `src/astro/runtime/manager.py`.
- [ ] Remove the process-wide `_web_client`.
- [ ] Remove per-session `MemorySearchResultStore` creation.
- [ ] Remove `build_web_tools(...)` from the durable-session base tool list.
- [ ] Remove the web-client close from manager shutdown.
- [ ] Delete `src/astro/tools/runtime_info.py` and remove its export from
      `src/astro/tools/__init__.py`.
- [ ] Remove `create_runtime_info_tool(...)` from the durable-session base tool list.
- [ ] Remove `runtime_info` instructions from `src/astro/resources/APPEND_SYSTEM.md`.
- [ ] Preserve extension diagnostics through structured manager logs and existing operator-facing
      health/diagnostic state; do not replace `runtime_info` with another model tool.
- [ ] Update `ProjectExtensionState` documentation so it describes operator-facing snapshots and
      logging rather than a model-facing consumer.
- [ ] Update manager tests so they no longer patch or expect file/web adapters or `runtime_info`.
- [ ] Add a tool-catalog assertion that all eight former tool names are absent without a project
      extension.
- [ ] Assert that Tau's core `read`, `write`, `edit`, and `bash` tools remain registered.
- [ ] Retain Main Sequence MCP tools and A2A task-control tools.
- [ ] Retain `httpx`; verify backend, MCP, provider, health, chat, and A2A code still uses it.

Acceptance:

- The default `CodingSession` contains none of `grep`, `find`, `ls`, `runtime_info`, `web_search`,
  `code_search`, `fetch_content`, or `get_search_content`.
- Tau core, Main Sequence MCP, and protocol-required task controls remain available.
- Assistant-ui chat and A2A streaming still operate over their existing HTTP paths.

### Phase 3: Delete the package and dependency graph

- [ ] Delete `packages/tau-file-tools` completely.
- [ ] Delete `packages/tau-web-access` completely.
- [ ] Remove both packages from root runtime dependencies and `[tool.uv.sources]`.
- [ ] Remove both packages from `[tool.uv.workspace].members`.
- [ ] Remove both test paths, mypy source paths, and coverage sources.
- [ ] Regenerate `uv.lock`.
- [ ] Regenerate `requirements-runtime.lock` from the new runtime dependency graph.
- [ ] Verify the locks no longer contain `tau-file-tools`, `tau-web-access`, or dependencies
      reachable only through them, including the HTML/YouTube/media packages present at removal
      time.
- [ ] Keep dependencies that have an independent Astro consumer; do not delete a package merely by
      name without checking the resolved graph.

Expected removable dependency families include:

- Beautiful Soup and SoupSieve;
- Markdownify and Six;
- YouTube Transcript API;
- Requests, urllib3, charset-normalizer, and defusedxml when no other consumer remains; and
- yt-dlp when it has not already been removed by overlapping work.

Acceptance:

- `uv tree --locked --package mainsequence-astro` has no file-tools or web-access edge.
- `pip check` passes in the release image.
- The active repository contains no importable `tau_file_tools` or `tau_web_access` source.

### Phase 4: Thin the image and local-development contract

- [ ] Remove both package copies and wheel builds from `Dockerfile`.
- [ ] Remove `tau_file_tools` and `tau_web_access` from runtime-image import checks.
- [ ] Remove both packages from `Dockerfile.remote-worker` import checks.
- [ ] Remove their Docker build-context exceptions.
- [ ] Remove their source mounts and `PYTHONPATH` entries from `docker-compose.yml`.
- [ ] Remove both package checks and web-only media checks from
      `scripts/verify-runtime-image.sh`.
- [ ] Keep `ripgrep` available to Tau's core `bash` tool unless a separate image-ABI decision
      removes it.
- [ ] Keep the executor's `ASTRO_CODE_REPOSITORY_EXTENSIONS_ENABLED=true` contract.
- [ ] Keep `/workspace/src:/workspace` and the shared `/opt/venv` contract unchanged.
- [ ] Add or retain an image fixture proving a project-owned extension can import an ordinary
      project-installed dependency and register a representative tool.
- [ ] Do not make the fixture depend on an Astro-owned web package.

Acceptance:

- The base and CodeRepository Executor overlay images start without either removed package.
- A project image can still add arbitrary Tau tools through `.tau/extensions`.

### Phase 5: Remove Astro-owned tests and preserve platform tests

- [ ] Delete `packages/tau-file-tools/tests` with the package.
- [ ] Delete `packages/tau-web-access/tests` with the package.
- [ ] Delete file/web adapter and `runtime_info` tests from `tests/unit/test_runtime_tools.py`; if
      no platform-owned tool tests remain, delete the file.
- [ ] Remove file/web adapter patches and `runtime_info` expectations from runtime-manager tests.
- [ ] Remove CI coverage/type-check references to both deleted packages.
- [ ] Retain Tau project-extension discovery, import, precedence, hook, and diagnostics tests.
- [ ] Add a negative base-catalog test for all eight removed names.
- [ ] Add a positive base-catalog test for Tau core and platform-required tools.
- [ ] Add a positive generic fixture extension test with a project-owned `AgentTool`.
- [ ] Verify direct and task-based A2A sessions receive the same project extension tool set as
      chat, closing the existing ADR 52 coverage gap.
- [ ] Verify the sessionless response path remains `tools=[]`.

Acceptance:

- Tests prove the minimal base catalog and generic extension contract rather than specific optional
  implementations.

### Phase 6: Correct active documentation

- [ ] Remove `tau-file-tools`, `tau-web-access`, and `runtime_info` from README architecture,
      package layout, system-prompt guidance, and quickstart text.
- [ ] Remove web-provider variables from `.env.example` and environment documentation.
- [ ] Update request-lifecycle and scope documentation so optional tools are project extensions.
- [ ] Update folder-structure and deployment documentation.
- [ ] Add an explicit supersession note to
      `docs/implemenation_task/remove-node-pi-migrate-to-tau.md` instead of rewriting its historical
      explanation of why commit `781ecf4` introduced the packages and diagnostics tool.
- [ ] Amend ADR 30's `get_runtime_info` requirements to point to operator-facing diagnostics and
      link ADR 55.
- [ ] Amend ADR 50's file-tool/image statements to remove package ownership while retaining the
      explicit `ripgrep` image decision.
- [ ] Amend ADR 52's fixed tool-composition examples to remove the eight base tools and link ADR
      55.
- [ ] Update changelog/release notes to describe the intentional tool-catalog break.
- [ ] Remove claims that Astro publishes, pins, verifies, or supports either removed wheel or the
      `runtime_info` model tool.

Acceptance:

- Current docs distinguish Astro platform transport and core coding tools from optional
  project-owned model tools.
- Historical material clearly says its file-tool, web-access, and model-facing runtime-info
  requirements were superseded by ADR 55.

### Phase 7: Verification and rollout

- [ ] Run formatting, Ruff, mypy, and the complete pytest suite.
- [ ] Build and run the standard Astro image verification.
- [ ] Build and run the CodeRepository Executor fixture verification.
- [ ] Search the active tree for `tau-file-tools`, `tau_file_tools`, `tau-web-access`,
      `tau_web_access`, all eight old tool names, YouTube/media-specific release checks, and
      web-provider environment variables.
- [ ] Classify every remaining match as either intentionally historical or project-owned test
      material.
- [ ] Smoke-test health, readiness, chat, A2A message, A2A Task, MCP, provider hydration, lease,
      persistence, and runtime shutdown.
- [ ] Cold-load one repository with no extensions and confirm only Tau core and platform-required
      tools are present.
- [ ] Cold-load one repository with a generic project extension and confirm registration,
      diagnostics, tool execution, and shutdown.
- [ ] Inventory deployed projects that intentionally relied on any removed tool and require them
      to opt in before rollout.
- [ ] Publish the removal as an intentional breaking tool-catalog change.

## Files Expected To Be Deleted

```text
packages/tau-file-tools/
packages/tau-web-access/
src/astro/tools/runtime_info.py
src/astro/tools/web_access.py
```

## Files Expected To Change

```text
.env.example
.github/workflows/quality.yml
CHANGELOG.md
Dockerfile
Dockerfile.dockerignore
Dockerfile.remote-worker
README.md
docker-compose.yml
pyproject.toml
requirements-runtime.lock
scripts/verify-runtime-image.sh
src/astro/resources/APPEND_SYSTEM.md
src/astro/runtime/extensions.py
src/astro/runtime/manager.py
src/astro/tools/__init__.py
tests/unit/test_runtime_manager_adr49.py
tests/unit/test_runtime_tools.py
uv.lock
docs/getting-started/*
docs/interface/environment.md
docs/reference/*
docs/adrs/adr-50-lean-python-runtime-abi.md
docs/adrs/adr-52-enable-repository-tau-extensions-in-code-executors.md
docs/implemenation_task/remove-node-pi-migrate-to-tau.md
deployment/gcp/README.md
```

The implementation must inspect current ownership before editing because some of these files may
contain unrelated or overlapping in-progress changes.

## Final Acceptance Criteria

- Astro has no source, dependency, image, test, or active-documentation ownership of
  `tau-file-tools`, web access, or model-facing runtime introspection.
- No default model-facing `grep`, `find`, `ls`, `runtime_info`, web/search/fetch/YouTube/video tool
  is registered.
- Tau's core `read`, `write`, `edit`, and `bash` tools remain registered.
- Main Sequence MCP and protocol-required A2A task controls remain registered.
- Astro HTTP/chat/A2A/backend/MCP/provider communication is unchanged and tested.
- `httpx` remains available for Astro's platform communication.
- Project-owned `.tau/extensions` can register arbitrary Tau tools from dependencies installed by
  the project image.
- Astro performs no runtime dependency installation or repair.
- Extension diagnostics remain visible and deterministic.
- `CodingSession.aclose()` runs on eviction so project cleanup hooks can execute.
- Sessionless responses remain tool-free.
- The release notes identify removal of all eight names as a breaking default-tool-catalog change.
