# Migration from the Former Deployment Package

This is a historical cutover guide. The old names below are not compatibility aliases.

| Former surface | Main Sequence TAU SDK |
| --- | --- |
| `mainsequence-astro` | `ms-tau-sdk` |
| `astro` import namespace | `ms_tau_sdk` |
| `astro-stream` | `ms-tau` |
| `ASTRO_CODE_REPOSITORY_CWD` | `MAINSEQUENCE_TAU_WORKSPACE` |
| other `ASTRO_*` settings | documented `MAINSEQUENCE_TAU_*` settings |
| image-selected runtime modes | one required project workspace |
| deployment extension flag | project Tau resources always enabled |
| package prompt assembler | Tau-native `.tau/SYSTEM.md` precedence |

The runtime credential names do not change:

```text
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET
```

## Removed rather than renamed

- project-owned Dockerfiles, Compose and Kubernetes assets, image publication, overlays, and
  runtime wheelhouses;
- no-workspace and runtime-role modes;
- compatibility import and command aliases;
- SDK-owned web/search/fetch/video/runtime-information tools; and
- the separate prompt and extension enable configuration.

A project now declares `ms-tau-sdk`, runs `ms-tau` from its root or imports `create_app`, and owns
its `.tau` behavior, extensions, optional tools, dependencies, and deployment artifact.
