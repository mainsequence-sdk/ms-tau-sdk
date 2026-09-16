# ADR: Editable Session Config Metadata

## Status

Superseded by ADR 51

ADR 51 removes the Astro-owned `PATCH /api/chat/session-config` write
contract. Canonical session configuration mutations now belong to Django. The
historical read-shape and editability context below is retained only as prior
design context; none of its Astro mutation routes remain implemented.

## Context

Astro already exposes `GET /api/chat/session-insights`, which returns the effective session state:

- `session`
- `model`
- `usage`
- `context`
- `lastTurn`
- `config`

The frontend now needs to let users adjust a small subset of session config, especially compaction
settings such as:

- `config.compaction.enabled`
- `config.compaction.reserveTokens`

However, a plain read-only `config` object is not enough for a good UI contract. The frontend also
needs to know:

- whether a field is editable
- what input type it should render
- numeric bounds and step size
- valid enum values when applicable
- units such as `tokens`

At the same time, we do not want to duplicate the full effective config in a separate write
endpoint just to tell the frontend what can be edited.

## Decision

Astro will keep `GET /api/chat/session-insights` as the canonical read endpoint for effective
session state, and it will extend that response with editability metadata.

That metadata should live alongside the read response and describe how the frontend may edit the
config fields already present in `session-insights`.

The read contract will therefore include an `editable` section that mirrors only the configurable
fields and describes their editing rules.

Example shape:

```json
{
  "config": {
    "compaction": {
      "enabled": true,
      "reserveTokens": 16384,
      "thresholdTokens": 111616,
      "thresholdPercent": 87.2
    }
  },
  "editable": {
    "config": {
      "compaction": {
        "enabled": {
          "editable": true,
          "type": "boolean"
        },
        "reserveTokens": {
          "editable": true,
          "type": "integer",
          "min": 1024,
          "max": 65536,
          "step": 1024,
          "unit": "tokens"
        },
        "thresholdTokens": {
          "editable": false,
          "type": "integer",
          "unit": "tokens"
        },
        "thresholdPercent": {
          "editable": false,
          "type": "number",
          "unit": "percent"
        }
      }
    }
  }
}
```

Astro will use a separate write endpoint for updates:

- `PATCH /api/chat/session-config`

That endpoint will accept only the editable subset and must not become a second source of truth for
the effective config shape.

The patch endpoint should therefore:

- accept only writable fields
- validate against the rules implied by the `editable` metadata
- persist overrides
- return a minimal success payload instead of re-sending the full effective config snapshot

The frontend should refetch `GET /api/chat/session-insights` after a successful patch.

## Initial editable scope

The initial editable surface should be intentionally small.

Editable first:

- `config.compaction.enabled`
- `config.compaction.reserveTokens`

Read-only for now:

- `config.compaction.thresholdTokens`
- `config.compaction.thresholdPercent`
- `config.model.provider`
- `config.model.model`
- `config.model.contextWindow`
- `config.model.maxOutputTokens`
- `usage`
- `context`
- `lastTurn`

`config.model.reasoningEffort` may become editable later, but it is not part of the initial
editable contract.

## Consequences

### Positive

- the frontend can render controls from the read response without hardcoded field rules
- effective config stays in one canonical read shape
- the write path stays narrow and intentionally limited
- field-level editability can evolve over time without changing the whole session-insights layout

### Negative

- the read response becomes slightly larger
- the backend must maintain metadata for both value and editability
- the write path still requires a second endpoint even though the read path advertises editability

## Follow-up

- add `editable` metadata to `GET /api/chat/session-insights`
- add `PATCH /api/chat/session-config`
- persist session-local overrides in session metadata
- derive threshold fields from effective values rather than making them directly writable

## Tasks

- [x] Capture the contract decision in an ADR.
- [x] Extend `GET /api/chat/session-insights` with `editable` metadata.
- [x] Add `PATCH /api/chat/session-config`.
- [x] Validate patch payloads against field metadata.
- [x] Persist session-level config overrides in metadata.
