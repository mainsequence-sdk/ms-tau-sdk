# Main Sequence TAU SDK

Main Sequence TAU SDK is the Python library that supplies the prepackaged Tau and Main Sequence
integration used by a normal project repository. The target distribution is `ms-tau-sdk`, the
import namespace is `ms_tau_sdk`, and the command is `ms-tau`.

This repository does not publish or own a runtime image. It contains no Dockerfile, Compose stack,
Kubernetes manifest, executor bundle, remote-worker overlay, or image-publication pipeline. A
consuming project declares and locks the SDK and owns its resulting deployable artifact.

## Migration Status

The implementation is being moved from the former Astro package under
[ADR 56](./docs/adrs/adr-56-main-sequence-tau-sdk-workspace-bound-library-deployment.md). During the
bounded extraction phases, the source still uses the temporary `mainsequence-astro`, `astro`, and
`astro-stream` names. They are not public compatibility promises for the new SDK.

Current migration evidence and phase status live in the
[ADR 56 migration workspace](./docs/migration/adr-56/README.md).

## SDK Responsibilities

The SDK provides:

- FastAPI application construction and lifecycle;
- Main Sequence runtime-credential exchange and backend access;
- provider validation and credential hydration;
- durable Tau sessions, leases, restore, persistence, cancellation, eviction, and shutdown;
- sessionless Tau execution;
- chat, responses, SSE, A2A, health, and readiness transports;
- Main Sequence MCP and protocol-required task controls; and
- packaged Tau defaults integrated with Tau's normal project configuration.

The consuming project owns its dependency lock, source and system dependencies, `.tau` overrides,
skills, prompts, hooks, extensions, and extension dependencies. Project code and the SDK execute in
the same trust boundary.

## Development

The repository currently requires Python 3.13 and `uv`:

```bash
uv sync --frozen
uv run pytest
uv run ruff check .
uv run mypy
```

Until Phase C3 introduces `ms-tau`, the current application can be exercised from the checkout with
the temporary command:

```bash
export MAINSEQUENCE_BACKEND="https://api.main-sequence.app"
export MAINSEQUENCE_RUNTIME_CREDENTIAL_ID="<development-runtime-credential-id>"
export MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET="<redeemed-once-secret>"

uv run astro-stream
```

Runtime credentials are exchanged for short-lived Main Sequence access tokens. Secrets must not be
committed to the repository or placed in `.tau` configuration.

## Target Project Usage

A project will declare and lock the SDK as a normal dependency:

```bash
uv add ms-tau-sdk
uv run ms-tau
```

The command runs from the project workspace. SDK-packaged Tau defaults are resolved with the
project's normal `.tau` configuration, including any project-owned extensions. There is no second
Main Sequence prompt or extension configuration system.

See the [source quickstart](./docs/getting-started/quickstart.md) and
[documentation index](./docs/README.md).
