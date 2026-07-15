# Superseded Task: External Main Sequence Pi Package

Status: Superseded

This task is no longer the active direction. ADR 40 replaces the external
package cutover with an adapter-owned Main Sequence Pi resource overlay located
at:

```text
adapters/mainsequence/pi-overlay
```

Main Sequence Astro images load that overlay with:

```text
ASTRO_PI_PACKAGE_PATHS=/app/adapters/mainsequence/pi-overlay
```

The active implementation work is tracked in ADR 40. Do not revive this task
unless the project explicitly decides to publish a standalone Main Sequence Pi
package later.
