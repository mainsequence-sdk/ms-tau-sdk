# Session Model

`GET /api/chat/session-model?sessionUid=<uid>` returns the provider and model in
the backend runtime state:

```json
{
  "sessionUid": "8fbc7a53-32e6-4eb8-9995-3b8040e2e314",
  "model": {
    "provider": "openai",
    "model": "gpt-5.4"
  }
}
```

This is a control-plane read and does not load or prompt Tau.
