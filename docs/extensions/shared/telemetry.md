# Shared telemetry

Source: [`pi/extensions/shared/telemetry.ts`](../../../pi/extensions/shared/telemetry.ts)

## Purpose

Serialize structured Astro telemetry events to stdout in a machine-readable format.

## Exports

- `TELEMETRY_PREFIX`
- `TelemetryPayload`
- `emitTelemetryEvent(name, data?)`

## Payload fields

- `name`
- `time`
- `role`
  - `parent` or `child`
- `specialist`
  - present when a child specialist is active
- `data`
  - event-specific payload

## Behavior

- no-op unless `ASTRO_TELEMETRY=1`
- writes one JSON line prefixed by `TELEMETRY_PREFIX`
- swallows serialization errors so telemetry cannot break the main run

## Related files

- [`../hooks/telemetry.md`](../hooks/telemetry.md)
- [`../../../interface/stream/server.ts`](../../../interface/stream/server.ts)
