# Session Config

`PATCH /api/chat/session-config` updates the editable subset of session-local config.

This endpoint is intentionally narrow. The canonical read shape comes from the backend-owned
session insights projection, which is produced by the checkpoint sidecar and advertises both the
current values and their editability metadata.

## Request

Canonical example:

```http
PATCH /api/chat/session-config
Content-Type: application/json
```

```json
{
  "sessionId": "39",
  "config": {
    "compaction": {
      "enabled": true,
      "reserveTokens": 24576
    }
  }
}
```

Accepted session id fields:

- `sessionId`
- `runtime_session_id`
- `runtimeSessionId`

## Initial writable fields

- `config.compaction.enabled`
- `config.compaction.reserveTokens`

All other fields shown in backend session insights remain read-only.

## Response

Example success response:

```json
{
  "ok": true,
  "sessionId": "39",
  "updatedAt": "2026-04-16T11:24:00.000Z",
  "updatedFields": [
    "config.compaction.enabled",
    "config.compaction.reserveTokens"
  ]
}
```

## Notes

- The frontend should refetch backend session insights after a successful patch.
- Updates are persisted in session metadata and applied to future turns through a session-scoped Pi
  settings overlay, rather than mutating the shared global Pi settings file.
