# Session Model Binding

`GET /api/chat/session-model` returns the model currently bound to an existing runtime session.

This is a control-plane endpoint.
It does not send a chat message to Pi.

`GET` accepts `sessionUid`, `runtime_session_uid`, or `runtimeSessionUid` as query parameters.

Model authority is session-first.
Normal chat turns should carry the current backend `AgentSession` serializer, and Astro derives or
refreshes its local binding from that session data.

## Response shape

`GET /api/chat/session-model` returns:

```json
{
  "sessionUid": "session_456_uid",
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
- `POST /api/chat` no longer treats a message-level `model` field as authoritative
- when the request includes a full `session` serializer, Astro compares
  `session.llm_provider` and `session.llm_model` to the currently stored binding and refreshes the
  local binding when they differ
- when the binding requires a custom provider registration, Astro passes a session-scoped env payload
  and the always-loaded runtime hook registers that provider before Pi resolves `--model`
- backend `llm_provider` and `llm_model` are now the canonical model identity fields for resumed
  sessions; `session_metadata.session_model_binding` is a cached normalized Astro projection
- when the runtime reports a different provider/model than the cached projection, Astro refreshes
  the local cached binding only; Astro does not write the model identity back into the backend
  session on the chat hot path
