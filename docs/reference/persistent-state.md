# Persistent State

Django is the canonical durable store for:

- agent sessions and runtime state
- native Tau session entries
- runtime leases
- provider credentials and their versions
- A2A tasks, status, messages, and artifacts

Astro containers are disposable. The project mount at `/workspace` is the only
long-lived filesystem visible to tools. `/tmp/astro-a2a-assets` contains
validated inline A2A files and `/tmp/astro-session-assets` contains
session-scoped backend skill materializations. Both may be discarded.

Provider credentials live only in backend responses and provider objects. Astro
does not write provider auth files or export provider secrets into global
environment variables.

Kubernetes uses one Astro container. There is no session-state volume,
checkpoint watcher, or sidecar.
