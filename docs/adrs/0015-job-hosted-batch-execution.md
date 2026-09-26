# ADR 0015: Job-Hosted Batch Execution

Status: Accepted — implementation pending

Date: 2026-09-26

Amends:

- [ADR 0001: Workspace-bound Python SDK](./0001-workspace-bound-python-sdk.md) by adding a
  Python batch entry point alongside the existing ASGI application;
- [ADR 0003: Tau-native project configuration](./0003-tau-native-project-configuration.md) by
  requiring batch execution to use the same effective project resources; and
- [ADR 0006: SDK-owned agent development skills](./0006-sdk-owned-agent-development-skills.md)
  by making job-hosted batch integration part of the version-matched repository skill.

## Context

The SDK currently starts a long-lived FastAPI application through `ms-tau` or `create_app`. Its
application lifespan composes Main Sequence authentication, provider evidence, optional MCP,
workspace-bound Tau resources, session storage, logging, and shutdown. A Main Sequence project can
also run a finite Job whose JobRun already owns process launch, status, cancellation, and runtime
logs. Starting Uvicorn inside that Job would add an HTTP listener and a service lifetime that the
Job does not need.

The upstream `tau --print` command runs a different composition. It does not automatically use the
installed `ms-tau-sdk` authentication, provider evidence, Main Sequence MCP adapter, or packaged
resources. A generic prompt wrapper around `tau_coding.CodingSession` would also make each project
reassemble SDK behavior. The required capability is one finite execution of the **same configured
Main Sequence TAU SDK runtime** in the Job's process.

Today's managed SDK session path assumes an AgentSession and a runtime credential. A standard
JobRun instead supplies a host-issued access-only credential. The backend currently rejects
AgentSession-less provider hydration with that credential. These are integration gaps; the JobRun
identity and lifecycle remain platform-owned and must not be copied into the SDK.

## Decision

### 1. Add a Python batch entry point to the SDK

The SDK will expose one supported async Python entry point for a project Job script:

```python
from ms_tau_sdk import TauSDKSettings, run_batch

answer = await run_batch(
    "Inspect this project's ingestion pipeline and fix the failing validation.",
    settings=TauSDKSettings(),
)
```

The intended public signature is:

```python
async def run_batch(
    instruction: str,
    *,
    settings: TauSDKSettings | None = None,
) -> str: ...
```

`instruction` is the work text selected by the project Job. It is not an SDK Task model, a
platform AgentTask, or a JobRun record. The Job may use a fixed instruction or resolve one from
its existing inputs. The SDK does not prescribe where that text is stored or how it reaches the
project script. The returned string is the final assistant text; the project decides whether and
where to present or persist it. Execution failure raises an exception for the host to handle.

The batch entry point does not call `create_app`, start Uvicorn, bind a port, serve HTTP routes, or
wait for requests. The existing no-argument `ms-tau` command and `create_app` remain the service
entry points. This ADR does not introduce or name a batch CLI command.

### 2. Reuse the configured SDK runtime composition

Batch execution uses `TauSDKSettings`, the selected workspace, packaged defaults, the project's
`.tau` resources, provider evidence and construction, tool composition, observability, and the Tau
session lifecycle already owned by the SDK. It must share their implementations rather than build
a second agent loop or invoke upstream `tau --print`.

The existing `TAU_EXCLUDE_BASE_TOOLS` and `TAU_EXCLUDE_MAINSEQUENCE_MCP` settings retain their
meanings. Batch execution does not force either tool source on. When MCP is excluded, startup and
execution do not connect to it. Project resources and extensions retain their existing Tau-native
resolution and trust behavior. Any A2A-only control requires a real A2A Task context; batch
execution must not manufacture one to expose those controls.

Batch invocation changes process lifetime, not the project's effective Tau behavior. The SDK
starts the configured dependencies needed for the one execution, runs it to a settled outcome,
finishes required SDK work, and closes sessions and clients before returning. It must not leave
background work running after the call returns. Cancellation and timeout stop the active turn and
propagate failure to the host; a forced process kill remains under the host's Job lifecycle.

### 3. Keep the Job above the SDK boundary

Main Sequence and the consuming project own Job and JobRun creation, image selection, the work
instruction, runtime configuration delivery, process limits, cancellation, status, log collection,
and any output record. The SDK receives its ordinary configuration and instruction. Its batch API
does not require a JobRun UID, query a JobRun, create an AgentTask or AgentSession, or write a
JobRun output. The project may use existing platform output APIs after `run_batch` returns.

The SDK emits its configured structured process logs. The Job's existing collector and trusted
workload metadata associate stdout and stderr with the JobRun. The SDK must not use an
AgentSession UID field to label a JobRun or infer JobRun ownership from an instruction or
environment string.

### 4. Resolve host authentication without importing Job ontology

The Job host supplies credentials and process settings through an SDK-supported authentication
contract. The SDK uses its existing backend and MCP transports under that authenticated principal;
it does not mint credentials or validate JobRun identity. A short-lived access-only credential
must not require a refresh token merely because the SDK's local development mode does.

Implementation requires a coordinated backend authorization contract for provider hydration
without an AgentSession, plus an SDK composition path that does not try to bootstrap a platform
AgentSession for a batch invocation. The backend remains authoritative for the host principal and
its permissions. This ADR approves the SDK execution boundary, not a particular new backend route,
schema, claim, or permission rule; those changes require their owning project's decision process.

## Project Job example

This is the target usage after the batch API is implemented. It is **not executable in the current
SDK release**:

```python
# jobs/solve_with_tau.py
import asyncio
from pathlib import Path

from ms_tau_sdk import TauSDKSettings, run_batch


async def main() -> None:
    answer = await run_batch(
        "Inspect this project's ingestion pipeline and fix the failing validation.",
        settings=TauSDKSettings(workspace=Path.cwd()),
    )
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())
```

The project chooses to print the answer in this example. It can instead handle the returned text
through its existing application or JobRun output mechanism. The SDK does neither automatically.

## Implementation and release gates

1. Extract or share application-scoped composition so the service lifespan and batch entry point
   use the same settings, provider, optional MCP, Tau resource, tool, logging, and cleanup paths.
2. Support the host credential and AgentSession-free batch bootstrap through explicit contracts;
   verify provider hydration and MCP authorization with the backend owner before release.
3. Verify that a batch invocation completes and exits without importing or launching Uvicorn,
   binding a port, or starting ASGI lifespan or HTTP background workers.
4. Verify the effective tool catalog with base tools enabled and excluded, and MCP enabled and
   excluded. Verify project `.tau` resources and extensions use normal SDK precedence.
5. Verify successful completion, propagated execution failure, cancellation, and dependency
   cleanup. Logs must be emitted through the configured process sinks without a JobRun identifier
   supplied to the SDK.
6. Publish the implemented Python API in the public API reference and a runnable project Job
   example in the batch guide. Update the packaged `tau_repository_integration` skill in the same
   release, so synchronized project instructions match the installed SDK version.

## Consequences

Projects can run the deployed Main Sequence Tau composition for finite work within their normal
Job execution and logging structure. The SDK gains one additional lifecycle entry point while the
platform continues to own scheduling, execution records, authorization, and JobRun observability.
