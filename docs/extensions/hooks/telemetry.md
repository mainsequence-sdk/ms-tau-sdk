# `telemetry`

Source: [`pi/extensions/hooks/telemetry/index.ts`](../../../pi/extensions/hooks/telemetry/index.ts)

## Purpose

Emit structured runtime telemetry events to stdout so Astro's stream layer and other consumers can observe hook, message, and tool activity without scraping human-readable output.

## Activation

- Enabled only when `ASTRO_TELEMETRY=1`

## Emitted events

- `session_start`
- `session_fork`
- `message_start`
- `message_update`
- `message_end`
- `tool_execution_end`

## Payload shape

The hook delegates serialization to [`emitTelemetryEvent`](../shared/telemetry.md), which adds:

- event name
- timestamp
- parent or child role
- active child agent type when present
- event-specific data

## Related files

- [`../shared/telemetry.md`](../shared/telemetry.md)
- [`../../interface/overview.md`](../../interface/overview.md)
