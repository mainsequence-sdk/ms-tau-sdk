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

## Agent-targeted sessionless response

`POST /api/agents/{agent_uid}/responses` accepts the canonical A2A message
envelope without `contextId` or any other session identity:

```json
{
  "message": {
    "messageId": "msg-1",
    "role": "ROLE_USER",
    "parts": [{"text": "Return a short status."}]
  },
  "configuration": {
    "acceptedOutputModes": ["text/plain"],
    "responseKind": "message"
  },
  "metadata": {
    "https://mainsequence.ai/a2a/extensions/agent-inference/v1": {
      "provider": "openai",
      "model": "gpt-5.4",
      "thinking": "medium",
      "timeoutSeconds": 120
    }
  }
}
```

Provider, model, and thinking inherit the deployment's immutable Agent snapshot
when omitted. The request creates no session, task, transcript, or checkpoint.
Use the standard output-contract metadata extension for strict JSON.
