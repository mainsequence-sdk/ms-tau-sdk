# Session Configuration

`PATCH /api/chat/session-config` changes the backend-owned provider and model
used by subsequent turns:

```json
{
  "sessionUid": "8fbc7a53-32e6-4eb8-9995-3b8040e2e314",
  "provider": "anthropic",
  "model": "claude-sonnet-4-5"
}
```

Astro evicts any loaded runtime before updating backend state so the next turn
constructs a new Tau session with the new provider.
