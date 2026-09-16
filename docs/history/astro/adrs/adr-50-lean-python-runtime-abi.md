# ADR 50: Adopt the Lean Python Runtime ABI

Status: Accepted and implemented

Date: 2026-08-29

Amended by ADR 55 on 2026-09-16: `ripgrep` remains a general repository utility available through
Tau's core `bash` tool; Astro no longer ships structured file-tool or web-access packages.

## Context

The CodeRepository Executor overlay inherited Jupyter base-image details that
were unrelated to Tau: `NB_*`, the Jovyan home, `SKEL_APP_DIR`, and `APP_DIR`.
Those names coupled Astro publication to the retired notebook image even
though Tau needs only Python, the verified CodeRepository checkout, and its
runtime data directories.

The platform cutover is defined by PodDeploymentOrchestrator ADR-0001 and
tdag-django ADR-047. Astro Tau owns the executor bundle and overlay that must
consume that contract before Django builds any executor from the new base.

## Decision

Astro Tau adopts this exact container ABI:

| Contract | Value |
| --- | --- |
| standalone base | digest-pinned `python:3.13-slim-bookworm` |
| user and group | `appuser`, UID/GID `10000:10000` |
| `APP_HOME` and `HOME` | `/home/appuser` |
| Python environment | `/opt/venv` |
| CodeRepository checkout and working directory | `/workspace` |
| immutable executor bundle | `/app`, root-owned |
| mutable executor state | `/session-state` |

The standalone image creates one venv and installs only builder-produced
wheels into it. The remote-worker overlay installs those same wheels into the
base image's `/opt/venv`; it never creates another interpreter or venv. Before
installation it verifies the base ABI and the clean Git branch, ref, and exact
commit at `/workspace`. The base must provide Git, while the remote-worker
overlay installs and owns `ripgrep` for repository work through Tau's core `bash` tool. FFmpeg and FFprobe
are workload-specific media tools and are not part of either the lean base ABI
or the Astro executor overlay. The final image ends as `USER 10000:10000` with
`WORKDIR /workspace`.

There is no compatibility user, symlink, path translation, or fallback for
Jovyan, `NB_*`, `SKEL_APP_DIR`, `APP_DIR`, `/opt/conda`, or the former
`user-skel` checkout. The executor wheelhouse is mounted from the bundle build
stage and is not retained in the final image. `/app` remains root-owned so the
runtime identity cannot mutate the executor bundle; the overlay explicitly
normalizes the copied bundle root even when the producer pre-creates `/app`
for the platform user.

Compose and Kubernetes examples use `/home/appuser`. Kubernetes explicitly
enforces the numeric non-root identity, RuntimeDefault seccomp, disabled
privilege escalation, and dropped Linux capabilities.

## Publication Order

Publish the updated executor bundle and `Dockerfile.remote-worker` while the
Django Python-runtime maintenance lock is active. Only then may Django prepare
executor images from the new `base_pod_images/python-py313` catalog base.
Pre-cutover executor images are destroyed by the coordinated Django cutover;
Astro Tau provides no mixed-ABI rollback image.

## Consequences

Tau and CodeRepository dependencies share one visible Python ABI, the executor
overlay no longer depends on Jupyter implementation details, and runtime
ownership agrees with Django's restricted container security context. This is
an intentional hard cut and requires coordinated publication with the base
image and fixed CodeRepository recipe.
