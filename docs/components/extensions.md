# Extensions

Astro Core runtime extensions live under `pi/extensions/`. Main Sequence package extensions live
under `adapters/mainsequence/pi-overlay/pi/extensions/`. The package files are the source of truth.

## Read by area

- [`../extensions/README.md`](../extensions/README.md)
- [`../extensions/hooks/README.md`](../extensions/hooks/README.md)
- [`../extensions/tools/README.md`](../extensions/tools/README.md)
- [`../extensions/shared/README.md`](../extensions/shared/README.md)

## Hooks

- [`agent-registration`](../extensions/hooks/agent-registration.md)
- [`telemetry`](../extensions/hooks/telemetry.md)

## Tools

- `runtime-info` lives at `pi/extensions/tools/runtime-info`.
- Main Sequence tools are exposed by `adapters/mainsequence/pi-overlay/package.json`.

## Shared helpers

- [`agent-registration`](../extensions/shared/agent-registration.md)
- [`telemetry`](../extensions/shared/telemetry.md)

## Runtime notes

- runtime entrypoints compose configured Pi packages through `ASTRO_PI_PACKAGE_PATHS`
- Docker Compose keeps the same extension surface while mounting the editable repo files into `/app`

## Related pages

- [`settings-and-system-prompt.md`](./settings-and-system-prompt.md)
- [`agents.md`](./agents.md)
- [`../getting-started/request-lifecycle.md`](../getting-started/request-lifecycle.md)
