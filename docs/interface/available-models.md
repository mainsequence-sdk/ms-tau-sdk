# Available Models

`GET /api/chat/get_available_models` returns the models Astro can currently offer without sending a
chat message to Pi.

This is a control-plane endpoint.
It is separate from `POST /api/chat`, which remains the hot path for user text.
It is also separate from `GET /api/models/catalog`, which returns Astro's global model catalog
without runtime availability filtering.

## Current discovery sources

The current implementation collects models from:

- Pi's actual available model registry, filtered to Astro-supported providers
- Ollama, when `OLLAMA_HOST` is configured

The endpoint is source-aware and supports partial failure.
If Ollama is unavailable, Pi-registry-backed models can still be returned and the Ollama failure
appears in the `sources` list.

## Response shape

```json
{
  "version": 2,
  "providers": [
    {
      "provider": "openai",
      "models": [
        {
          "source": "pi-model-registry",
          "provider": "openai",
          "label": "gpt-5.4",
          "model": "gpt-5.4",
          "available": true,
          "auth": {
            "required": true,
            "authKind": "api_key",
            "signInAvailable": false,
            "authenticated": true,
            "usable": true,
            "authSource": "runtime_store"
          },
          "defaults": {
            "runConfig": {
              "reasoning_effort": "medium"
            }
          },
          "capabilities": {
            "runConfig": {
              "reasoning_effort": {
                "supported": true,
                "mode": "levels",
                "values": ["off", "minimal", "low", "medium", "high", "xhigh"],
                "default": "medium"
              }
            }
          }
        }
      ]
    },
    {
      "provider": "ollama",
      "models": [
        {
          "source": "ollama",
          "provider": "ollama",
          "label": "qwen2.5-coder:7b",
          "model": "qwen2.5-coder:7b",
          "available": true,
          "defaults": {
            "runConfig": {
              "reasoning_effort": "off"
            }
          },
          "capabilities": {
            "features": ["completion", "thinking"],
            "runConfig": {
              "reasoning_effort": {
                "supported": true,
                "mode": "toggle",
                "values": ["off", "on"],
                "default": "off"
              }
            }
          },
          "metadata": {
            "ollama_host": "http://localhost:11434"
          }
        }
      ]
    }
  ],
  "sources": [
    {
      "source": "pi-model-registry",
      "ok": true,
      "count": 1,
      "details": {
        "auth_providers": ["openai"],
        "available_model_count": 1
      }
    },
    {
      "source": "ollama",
      "ok": true,
      "count": 2,
      "details": {
        "host": "http://localhost:11434",
        "listed_model_count": 2,
        "inspected_model_count": 2,
        "inspection_error_count": 0
      }
    }
  ]
}
```

## Notes

- Pi-registry discovery now starts from Pi's registry of known models, filters that list to
  Astro-supported providers, and then annotates auth-backed models with Astro runtime auth state
- auth-backed Pi models only appear here when they are actually usable right now
- the endpoint does not synthesize a fake default provider/model entry when no auth-backed Pi model
  is available
- Ollama discovery uses `GET <OLLAMA_HOST>/api/tags`, then enriches each listed model with `POST <OLLAMA_HOST>/api/show`
- if `OLLAMA_HOST` includes `/v1`, Astro strips that suffix before calling the Ollama native `/api/*` routes
- `defaults.runConfig` describes the default runtime settings Astro would use for that model
- `capabilities.runConfig.reasoning_effort` describes the reasoning levels Astro currently knows how
  to offer for that model
- `providers[*].provider` is the top-level grouping key for the frontend
- `providers[*].models[*]` contains the actual selectable runtime models for that provider
- `auth.required` is present for auth-backed Pi providers such as `openai` and `anthropic`
- `auth.authKind` tells the frontend whether the provider is API-key-backed or OAuth-backed
- `auth.signInAvailable` is the frontend-friendly field for whether `POST .../signin` can start right now
- `auth.authenticated` means Astro currently allows that provider to execute
- `auth.usable` means the model can be executed right now
- signing a provider off removes that provider's auth-backed models from this endpoint; those models
  still remain visible in `GET /api/models/catalog`
- unsupported Pi-registry providers are intentionally omitted from this endpoint
- `capabilities.features` carries the provider-reported capability list when Astro can collect it
- Ollama models that report `thinking` or `reasoning` through `/api/show` are exposed as reasoning
  `mode: "toggle"` with values `["off", "on"]`
- Ollama models without those features are exposed as reasoning `mode: "unsupported"`
- `sources[*].error` is present when a source fails
- the endpoint returns `200` when discovery completes normally, even if one source failed
- unexpected server failures return `500`
