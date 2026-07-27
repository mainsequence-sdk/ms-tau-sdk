# Request Contract

## Durable assistant-ui chat

`POST /api/chat` requires an existing backend session UID and a user message:

```json
{
  "sessionUid": "8fbc7a53-32e6-4eb8-9995-3b8040e2e314",
  "messages": [
    {
      "role": "user",
      "content": [{"type": "text", "text": "Summarize this project."}]
    }
  ]
}
```

Accepted session aliases are `sessionUid`, `runtime_session_uid`,
`runtimeSessionUid`, and `agent_session_uid`. A top-level string `message` can
be used instead of `messages`.

Astro attaches to the existing session. It never allocates a backend session
from the chat request.

## Stateless model chat

`POST /api/llm/chat` requires `x-mainsequence-user-uid`:

```json
{
  "model": "gpt-5.4",
  "messages": [{"role": "user", "content": "Return a short status."}],
  "max_tokens": 200,
  "metadata": {
    "astro": {
      "provider": "openai",
      "timeout_seconds": 120
    }
  }
}
```

Set `response_format` to `json` or `json_object` for strict JSON validation and
bounded repair attempts.
