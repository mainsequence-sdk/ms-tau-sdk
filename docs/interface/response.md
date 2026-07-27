# Response Contract

`POST /api/chat` returns `text/event-stream` using the assistant-ui data-stream
protocol. Astro translates Tau events into text, reasoning, tool, finish, and
error chunks. The response always terminates with:

```text
data: [DONE]
```

Tau implementation objects and raw provider events are never exposed.

`POST /api/llm/chat` returns JSON:

```json
{
  "ok": true,
  "provider": "openai",
  "model": "gpt-5.4",
  "message": {"role": "assistant", "content": "Ready."},
  "finish_reason": "stop",
  "usage": {
    "input_tokens": 10,
    "output_tokens": 2,
    "total_tokens": 12
  }
}
```

Strict JSON requests also include a parsed `json` field.
