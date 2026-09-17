---
name: tau-repository-integration
description: Install, expose, run, and validate the version-matched Main Sequence TAU SDK inside a project repository without redefining platform deployment semantics.
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

## Runtime entry points

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

## Repository integration procedure

1. Inspect the project's Python constraint, dependency manager, lockfile, and application layout.
2. Add and pin `ms-tau-sdk` through that existing dependency workflow.
3. Synchronize these skills explicitly with `uv run ms-tau skills sync --path .`.
4. Use `uv run ms-tau` unless the repository needs an importable ASGI module.
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
