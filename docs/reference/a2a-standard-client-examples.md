# A2A Standard Client Examples

These examples use Astro's public A2A adapter, not Astro's internal runtime implementation details.
They call `POST /api/a2a/v1/message:send` for direct request/response turns and never call
`/api/chat`, `/api/a2a/chat`, or any runtime chat route.

Phase 1 requires `message.contextId` to be the existing Main Sequence `AgentSession.uid`.
Clients must generate `message.messageId` and reuse the same value when retrying the same
logical message. This preserves request identity, but a direct Message send is not durably
replay-safe and must not be retried automatically after an ambiguous timeout; select Task mode
when durable recovery is required.
The backend-owned `AgentSession` remains the authority for runtime identity, authorization,
checkpointing, credentials, and session continuity.

## REST: Direct Message

```bash
curl -sS \
  -X POST "$ASTRO_BASE_URL/api/a2a/v1/message:send" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/a2a+json" \
  -H "Accept: application/a2a+json" \
  -H "A2A-Extensions: https://mainsequence.ai/a2a/extensions/response-kind/v1" \
  -d '{
    "message": {
      "messageId": "msg-client-1",
      "role": "ROLE_REQUESTER",
      "contextId": "'"$AGENT_SESSION_UID"'",
      "parts": [
        {
          "text": "Return a JSON dictionary describing what you can do."
        }
      ]
    },
    "configuration": {
      "acceptedOutputModes": ["application/json"],
      "responseKind": "message"
    },
    "metadata": {
      "https://mainsequence.ai/a2a/extensions/output-contract/v1": {
        "strict": true,
        "schema": {
          "type": "object",
          "additionalProperties": true
        }
      }
    }
  }'
```

Successful responses contain exactly one top-level result branch:

```json
{
  "message": {
    "messageId": "msg-agent-1",
    "role": "ROLE_RESPONDER",
    "contextId": "agent-session-uid",
    "parts": [
      {
        "data": {
          "summary": "Astro can coordinate Main Sequence agent work."
        },
        "mediaType": "application/json"
      }
    ]
  }
}
```

## REST: Async Task

Set `configuration.responseKind` to `task` when the caller wants a durable asynchronous Task.
Send the response-kind extension URI in `A2A-Extensions` as shown in the direct example.

```json
{
  "message": {
    "messageId": "msg-client-2",
    "role": "ROLE_REQUESTER",
    "contextId": "agent-session-uid",
    "parts": [
      {
        "text": "Analyze this longer request and return a task artifact."
      }
    ]
  },
  "configuration": {
    "acceptedOutputModes": ["text/plain"],
    "responseKind": "task"
  }
}
```

Poll the task:

```bash
curl -sS \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/a2a+json" \
  "$ASTRO_BASE_URL/api/a2a/v1/tasks/$TASK_ID"
```

## REST: Streaming

```bash
curl -N -sS \
  -X POST "$ASTRO_BASE_URL/api/a2a/v1/message:stream" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/a2a+json" \
  -H "Accept: text/event-stream" \
  -d '{
    "message": {
      "messageId": "msg-client-stream-1",
      "role": "ROLE_REQUESTER",
      "contextId": "'"$AGENT_SESSION_UID"'",
      "parts": [
        {
          "text": "Stream task progress and final result."
        }
      ]
    },
    "configuration": {
      "acceptedOutputModes": ["text/plain"]
    }
  }'
```

Each SSE `data` frame contains an A2A object such as `{ "task": ... }`; Astro/Pi internal chunks
are not part of this public stream.

## JSON-RPC: SendMessage

```bash
curl -sS \
  -X POST "$ASTRO_BASE_URL/api/a2a/rpc" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "A2A-Extensions: https://mainsequence.ai/a2a/extensions/response-kind/v1" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "SendMessage",
    "params": {
      "message": {
        "messageId": "msg-client-rpc-1",
        "role": "ROLE_REQUESTER",
        "contextId": "'"$AGENT_SESSION_UID"'",
        "parts": [
          {
            "text": "Hello"
          }
        ]
      },
      "configuration": {
        "acceptedOutputModes": ["text/plain"],
        "responseKind": "message"
      }
    }
  }'
```

The JSON-RPC response wraps the same A2A `message` or `task` object under `result`.

## Runtime Boundary

Public A2A clients should use only the standard REST or JSON-RPC routes shown above. Astro maps
`message.contextId` to the existing Main Sequence `AgentSession.uid` internally; runtime bootstrap,
warm-runner reuse, checkpointing, and credential hydration are not separate public A2A calls.
