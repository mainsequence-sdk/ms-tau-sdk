# ADR 0001: Workspace-Bound Python SDK

Status: Accepted

Date: 2026-09-16

## Context

Main Sequence projects already own their Python environment and execute arbitrary trusted project
code. Maintaining a second image product obscures the dependency version, complicates debugging,
and creates no meaningful isolation boundary.

## Decision

Main Sequence TAU SDK is a Python library with these identities:

| Surface | Value |
| --- | --- |
| Distribution | `ms-tau-sdk` |
| Import namespace | `ms_tau_sdk` |
| Process command | `ms-tau` |
| Initial version | `0.1.0` |
| First stable version | `1.0.0` |

The supported public construction surface is:

```python
from ms_tau_sdk import TauSDKSettings, create_app
```

Every process is bound to one existing readable project workspace. The workspace defaults to the
current directory and may be selected with `MAINSEQUENCE_TAU_WORKSPACE` or `TauSDKSettings`.

The SDK supplies the complete accepted Python behavior: application lifecycle, Main Sequence
authentication client, backend and MCP transports, provider construction, durable and sessionless
Tau execution, persistence, streaming, A2A, cancellation, observability, and shutdown. A project
does not rewrite those integrations.

This repository publishes a wheel and source distribution. It owns no container, Compose stack,
Kubernetes manifest, image overlay, runtime wheelhouse, or deployment system. A consuming project
owns its application artifact and deployment mechanism.

External Main Sequence services and their schemas are dependencies, not implementation targets of
this decision.

## Consequences

- A project pins an inspectable SDK dependency in its normal lockfile.
- `ms-tau` and `create_app` construct the same application and lifecycle.
- SDK upgrades use normal dependency updates.
- Projects must provide a compatible Python runtime and all project-specific dependencies.
- Changes to external services require separate authorization and decisions.
