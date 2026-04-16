# Session Insights

`GET /api/chat/session-insights` returns one runtime-session snapshot derived from the local Pi
session file.

Accepted query parameters:

- `sessionId`
- `runtime_session_id`
- `runtimeSessionId`

Canonical example:

```http
GET /api/chat/session-insights?sessionId=39
```

Example response:

```json
{
  "version": 1,
  "session": {
    "sessionId": "39",
    "threadId": "__LOCALID_R0NXTWK",
    "agentName": "astro-orchestrator",
    "agentId": 1,
    "agentSessionId": 39,
    "status": "completed",
    "startedAt": "2026-04-15T09:34:38.000Z",
    "updatedAt": "2026-04-15T09:34:46.000Z",
    "lastError": null
  },
  "model": {
    "provider": "ollama",
    "model": "deepseek-r1:32b",
    "reasoningEffort": "off",
    "contextWindow": 128000,
    "maxOutputTokens": 16384
  },
  "usage": {
    "userMessages": 1,
    "assistantMessages": 1,
    "assistantTurns": 1,
    "toolCalls": 0,
    "toolResults": 0,
    "totalMessages": 2,
    "tokens": {
      "input": 1200,
      "output": 240,
      "cacheRead": 0,
      "cacheWrite": 0,
      "total": 1440
    },
    "estimatedCostUsd": null
  },
  "context": {
    "status": "known",
    "source": "provider_usage_plus_estimate",
    "tokens": 18000,
    "contextWindow": 128000,
    "percentOfContextWindow": 14.1,
    "compactionEnabled": true,
    "compactionReserveTokens": 16384,
    "compactionThresholdTokens": 111616,
    "tokensRemainingBeforeCompaction": 93616,
    "tokensRemainingBeforeContextLimit": 110000,
    "latestCompaction": null
  },
  "config": {
    "compaction": {
      "enabled": true,
      "reserveTokens": 16384,
      "thresholdTokens": 111616,
      "thresholdPercent": 87.2
    },
    "model": {
      "provider": "ollama",
      "model": "deepseek-r1:32b",
      "reasoningEffort": "off",
      "contextWindow": 128000,
      "maxOutputTokens": 16384
    }
  },
  "editable": {
    "config": {
      "compaction": {
        "enabled": {
          "editable": true,
          "type": "boolean"
        },
        "reserveTokens": {
          "editable": true,
          "type": "integer",
          "min": 1024,
          "max": 126976,
          "step": 1024,
          "unit": "tokens"
        },
        "thresholdTokens": {
          "editable": false,
          "type": "integer",
          "unit": "tokens"
        },
        "thresholdPercent": {
          "editable": false,
          "type": "number",
          "unit": "percent"
        }
      },
      "model": {
        "provider": {
          "editable": false,
          "type": "string"
        },
        "model": {
          "editable": false,
          "type": "string"
        },
        "reasoningEffort": {
          "editable": false,
          "type": "string"
        },
        "contextWindow": {
          "editable": false,
          "type": "integer",
          "unit": "tokens"
        },
        "maxOutputTokens": {
          "editable": false,
          "type": "integer",
          "unit": "tokens"
        }
      }
    }
  },
  "info": {
    "config": {
      "label": "Config",
      "description": "The effective session config Astro uses when building future turns for this session.",
      "children": {
        "compaction": {
          "label": "Compaction Config",
          "description": "The effective automatic-compaction policy currently applied to this session."
        }
      }
    },
    "context": {
      "label": "Context",
      "description": "The current estimated context occupancy and compaction headroom for the active session branch."
    }
  },
  "lastTurn": {
    "completedAt": "2026-04-15T09:34:46.000Z",
    "finishReason": "stop",
    "errorMessage": null,
    "model": {
      "provider": "ollama",
      "model": "deepseek-r1:32b"
    },
    "tokens": {
      "input": 1200,
      "output": 240,
      "cacheRead": 0,
      "cacheWrite": 0,
      "total": 1440
    }
  }
}
```

Notes:

- `usage.tokens.total` is cumulative token usage across the runtime session.
- `context.tokens` is the current estimated context occupancy.
- `config.compaction` exposes the effective compaction policy used for this session.
- `config.model` exposes the effective model limits and reasoning setting used for this session.
- `editable` describes which config fields are writable and how they may be edited.
- `info` mirrors the session-insights shape with labels, descriptions, and source references so the
  frontend can explain the fields without hardcoding copy.
- `PATCH /api/chat/session-config` applies updates to the writable subset advertised by `editable`.
- `context.tokensRemainingBeforeCompaction` is the remaining budget before Pi's automatic
  compaction threshold is crossed.
- `context.tokensRemainingBeforeContextLimit` is the remaining budget before the hard context
  limit.
- `context.status: "unknown_after_compaction"` means the session compacted and Astro does not yet
  trust a fresh current-context number.

## `info` node shape

Each `info` entry is a node with:

- `label`
- `description`
- optional `source`
- optional `children`

Example source entry:

```json
{
  "kind": "pi_doc",
  "package": "pi-coding-agent",
  "path": "README.md",
  "section": "Compaction"
}
```
