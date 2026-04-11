# Request Contract

`POST /api/chat` accepts a JSON body compatible with assistant-ui's `ui-message-stream` request shape.

## Required fields

- `agentName` (string)
- `userId` (string | number)
- `messages` (array) — the last entry must be the current user message

## Optional fields

- `system` (string)
- `threadId` (string)
- `newChat` (boolean)
- `context` (object)
- `tools` (object)
- `runtime_session_id` (string; required when `newChat` is `false`)

## Notes

- The server reads **only the last entry** in `messages`.
- The last entry must be a `user` role message.
- `newChat` is treated as a UI hint for a new conversation.
- `threadId` is returned for client bookkeeping, but session continuity is tied to the backend agent identity when registration is enabled.
- `agentName` must match the backend registry (unknown agents return `error: unknown_agent`).

## Example request

```json
{
  "threadId": "thread-001",
  "newChat": true,
  "agentName": "astro-orchestrator",
  "userId": "user_123",
  "system": "optional system prompt",
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "Summarize this workspace." }
      ]
    }
  ],
  "tools": {},
  "context": {
    "appId": "astro-ui",
    "surfaceId": "command-center",
    "userId": "user_123"
  }
}
```
