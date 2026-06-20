# Main Sequence Astro Pi Overlay

This directory is the Main Sequence adapter-owned Pi resource overlay for Astro.
It is loaded by Main Sequence Astro deployments through `ASTRO_PI_PACKAGE_PATHS`.

This is not Astro Core and it is not an external portable Pi package. It contains
Main Sequence-specific Pi resources that are only composed when the Main Sequence
adapter/image is selected.

## Contents

- `pi/extensions/hooks/scaffold-skill-discovery`: asks the Main Sequence SDK CLI
  to seed SDK-owned skills into the runtime `.agents/skills` directory.
- `pi/extensions/hooks/project-policy`: adds Main Sequence project policy when
  the runtime is attached to a project workspace.
- `pi/extensions/tools/mainsequence-cli-auth`: exposes Main Sequence CLI auth
  repair behavior to Pi.
- `pi/extensions/tools/mainsequence-runtime-info`: exposes Main Sequence runtime
  metadata to Pi.
- `pi/prompts`: Main Sequence reusable prompt templates.
- `pi/system/APPEND_SYSTEM.md`: Main Sequence Astro runtime contract appended to
  the Pi system prompt.

## Runtime Contract

Main Sequence deployments point at this overlay with:

```sh
ASTRO_PI_PACKAGE_PATHS=/app/adapters/mainsequence/pi-overlay
```

Local development can point at the repository path:

```sh
ASTRO_PI_PACKAGE_PATHS=./adapters/mainsequence/pi-overlay
```

The SDK remains the source of truth for SDK-owned skills. This overlay only owns
the Pi hooks and tools needed to request those skills and compose Main Sequence
behavior into Astro.
