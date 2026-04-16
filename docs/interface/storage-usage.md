# Storage Usage

`GET /api/storage/usage` returns a global storage snapshot for Astro's durable runtime root.

This endpoint is global, not session-specific.
It reports against `ASTRO_CONTAINER_DATA_DIR`, which in containerized deployments is the PVC-like
runtime root at `/root/.astro-container-data`.

Canonical example:

```http
GET /api/storage/usage
```

Example response:

```json
{
  "version": 1,
  "root": "/root/.astro-container-data",
  "totalBytes": 274877906944,
  "availableBytes": 201863462912,
  "filesystemUsedBytes": 73014444032,
  "filesystemUsagePercent": 26.56,
  "consumedBytes": 1852743296,
  "consumedPercentOfTotal": 0.67,
  "detail": {
    "pi": { "bytes": 10485760 },
    "astro": { "bytes": 5242880 },
    "sessions": { "bytes": 73400320 },
    "system": { "bytes": 1769998336 }
  },
  "capturedAt": "2026-04-16T10:20:00.000Z"
}
```

Field meanings:

- `totalBytes`
  - total capacity of the underlying filesystem or PVC
- `availableBytes`
  - bytes currently available to Astro on that filesystem
- `filesystemUsedBytes`
  - filesystem-level used space derived from the same root mount
- `consumedBytes`
  - Astro-managed bytes currently stored under the runtime root
- `detail.pi.bytes`
  - `.pi/agent` except `sessions/`
- `detail.astro.bytes`
  - `.astro` except `stream-sessions/`
- `detail.sessions.bytes`
  - Pi `sessions/` plus Astro `stream-sessions/`
- `detail.system.bytes`
  - everything else under the durable runtime root, such as:
    - `.config/mainsequence`
    - `mainsequence`
    - `mainsequence-dev`
    - `uv`

Notes:

- `filesystemUsedBytes` and `consumedBytes` are intentionally different metrics.
- `filesystemUsedBytes` reflects the mounted filesystem or PVC.
- `consumedBytes` reflects the bytes Astro can attribute to its own managed runtime tree.
- In local Docker, this endpoint reports against the named volume mounted at
  `/root/.astro-container-data`, which is intended to simulate the deployed PVC layout.
