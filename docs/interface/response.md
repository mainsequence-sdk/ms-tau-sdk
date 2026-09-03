# Response Contract

`POST /api/chat` returns `text/event-stream` using the assistant-ui data-stream
protocol. Astro translates Tau events into text, reasoning, tool, finish, and
error chunks. The response always terminates with:

```text
data: [DONE]
```

Tau implementation objects and raw provider events are never exposed.

`POST /api/agents/{agent_uid}/responses` returns the direct A2A Message branch:

```json
{
  "message": {
    "kind": "message",
    "messageId": "msg-agent-1",
    "role": "ROLE_RESPONDER",
    "parts": [{"text": "Ready."}],
    "metadata": {
      "https://mainsequence.ai/a2a/extensions/agent-inference/v1": {
        "agentUid": "11111111-1111-4111-8111-111111111111",
        "resolved": {
          "provider": "openai",
          "model": "gpt-5.4",
          "thinking": "medium"
        }
      }
    }
  }
}
```

Strict JSON is returned as a `Part.data` object. The stream endpoint buffers the
same result and emits one final A2A Message SSE event.
