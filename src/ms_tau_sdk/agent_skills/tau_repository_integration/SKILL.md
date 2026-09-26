---
name: tau-repository-integration
description: Install, run, and validate the version-matched Main Sequence TAU SDK in a project repository, including service entry points and job-hosted batch integration.
---

# TAU Repository Integration

Use this skill when adding or repairing the TAU runtime inside a repository. It owns SDK and
workspace mechanics. The platform-owned `code_repository_to_agent` and
`code_repository_workflows` skills remain authoritative for Agent identity, source cards,
ResourceReleases, workflow declarations, authorization, and deployment.

## Synchronize these instructions

The consuming project must pin `ms-tau-sdk` through its normal dependency manager. After the
dependency is installed, explicitly synchronize the instructions supplied by that installed
version:

```bash
uv run ms-tau skills sync --path .
```

This command atomically owns only `.agents/skills/ms_tau_sdk/`. Do not copy these files manually,
edit the managed copies, or make installation and runtime startup synchronize them implicitly.

## Service entry points

The normal process entry point is:

```bash
uv run ms-tau
```

With no subcommand, `ms-tau` starts the SDK application in the current workspace. A project that
needs an importable ASGI module may create a thin module such as `api/tau/main.py`:

```python
from ms_tau_sdk import create_app

app = create_app()
```

Both paths construct the same application. The project owns the dependency lock, workspace,
deployable artifact, system packages, and any surrounding ASGI composition. There is no separate
TAU image, overlay, or SDK-owned deployment.

## Job-hosted batch execution

ADR 0015 accepts a Python batch entry point, but it is **not implemented in this SDK version**.
Do not describe the example below as executable until the installed SDK exports `run_batch` and
its backend authorization dependency is available. The current `ms-tau` command starts the ASGI
service; upstream `tau --print` does not construct this SDK's authenticated runtime.

The intended project Job script supplies its work instruction and normal `TauSDKSettings`:

```python
import asyncio
from pathlib import Path

from ms_tau_sdk import TauSDKSettings, run_batch  # planned API


async def main() -> None:
    answer = await run_batch(
        "Inspect this project's ingestion pipeline and fix the failing validation.",
        settings=TauSDKSettings(workspace=Path.cwd()),
    )
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())
```

The project and Main Sequence Job own instruction selection, credentials and settings delivery,
JobRun lifecycle, status, logs, and any output record. The SDK batch entry point must use the same
effective `.tau` resources, provider, and configured tool composition as the service, then close
and exit. `TAU_EXCLUDE_BASE_TOOLS` and `TAU_EXCLUDE_MAINSEQUENCE_MCP` retain their existing
meanings; batch execution must not force either source on. It does not start Uvicorn or require a
JobRun UID in the SDK.

## Repository integration procedure

1. Inspect the project's Python constraint, dependency manager, lockfile, and application layout.
2. Add and pin `ms-tau-sdk` through that existing dependency workflow.
3. Synchronize these skills explicitly with `uv run ms-tau skills sync --path .`.
4. For a service, use `uv run ms-tau` unless the repository needs an importable ASGI module.
   For a finite Job, follow the batch design only after the installed SDK supports it.
5. Keep the application module thin; business behavior and project tools belong to project code.
6. Read `tau-project-customization` before changing `.tau`.
7. Read `tau-local-development` before running an authenticated local conversation.
8. Return to the platform-owned skills for source-card, workflow, synchronization, deployment,
   and runtime-log operations.

## Validation

From the project root:

```bash
uv sync --locked
uv run python -c "import ms_tau_sdk; print(ms_tau_sdk.__version__)"
uv run ms-tau skills list
```

When an ASGI shim exists, also import it without starting a second application implementation:

```bash
uv run python -c "from api.tau.main import app; print(type(app).__name__)"
```

Do not require the `mainsequence` Python distribution in the TAU dependency graph. Authentication
and Main Sequence transport are implemented directly by `ms-tau-sdk`.
