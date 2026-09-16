# Session Configuration

Astro does not expose a session-selection mutation route. Callers change the
Django-owned selection directly:

```http
PATCH /api/v1/agent-sessions/{agent_session_uid}/runtime-state/
```

```json
{
  "active_provider": "anthropic",
  "active_model": "claude-sonnet-4-6",
  "active_thinking": "high"
}
```

Django validates and persists the complete canonical selection atomically. It
rejects changes while a turn is working or persisting and invalidates a loaded
idle runtime lease. Astro reloads that session once before inference if a turn
races with the mutation; the caller sends only the one Django mutation.
