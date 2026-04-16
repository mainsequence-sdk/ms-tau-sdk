# Session Model Binding

`GET /api/chat/session-model` returns the model currently bound to an existing runtime session.

This is a control-plane endpoint.
It does not send a chat message to Pi.

`GET` accepts `sessionId`, `runtime_session_id`, or `runtimeSessionId` as query parameters.

Model changes happen on `POST /api/chat` by including the optional lightweight `model` object on
the same request as the user message.

## Response shape

`GET /api/chat/session-model` returns:

```json
{
  "sessionId": "456",
  "model": {
    "source": "ollama",
    "provider": "ollama",
    "label": "deepseek-coder:33b",
    "model": "deepseek-coder:33b",
    "runConfig": {
      "reasoning_effort": "off"
    },
    "capabilities": {
      "reasoning_effort": {
        "supported": false,
        "mode": "unsupported",
        "values": [],
        "default": "off"
      }
    },
    "metadata": {
      "ollama_host": "http://192.168.1.10:11434"
    },
    "updatedAt": "2026-04-14T12:00:00.000Z",
    "piThinkingLevel": "off"
  }
}
```

## Notes

- the stored binding is the launch-time source of truth for Pi on resumed turns
- `POST /api/chat` uses the stored binding first and only falls back to the agent frontmatter model
  when no session model is bound
- if the request includes `model`, Astro persists that binding before launching Pi for the turn
- when the binding requires a custom provider registration, Astro passes a session-scoped env payload
  and the always-loaded runtime hook registers that provider before Pi resolves `--model`
- when a model binding is supplied on a brand-new session, Astro also includes it in backend
  `session_metadata` and uses it for backend `llm_provider`, `llm_model`, and
  `runtime_config_snapshot`
- updating an already-created backend `AgentSession` record after the fact is still a follow-up
