# Model Catalog

`GET /api/models/catalog` returns Astro's global model catalog. Auth metadata is user-scoped when
`created_by_user_uid=<user_uid>` is passed.

This is a control-plane endpoint.
It is not the same as `GET /api/chat/get_available_models`.

Use it when the frontend needs the full set of model possibilities Astro exposes in-product, even
when a provider is not currently signed in.

## What it includes

The current implementation returns Pi registry models for Astro-supported providers and annotates
auth-backed entries with backend-owned provider credential metadata.

It does not use runtime availability filtering.
That means models can appear here even when they are not executable right now.

## When to use it

Use `GET /api/models/catalog` for:

- global model/settings screens
- provider/model setup flows
- inspecting all model possibilities Astro currently supports in the UI

Use `GET /api/chat/get_available_models` for:

- chat runtime model pickers
- only-show-what-can-be-offered-now flows

## Response shape

```json
{
  "version": 1,
  "models": [
    {
      "source": "pi-model-registry",
      "provider": "openai-codex",
      "label": "gpt-5-codex",
      "model": "gpt-5-codex",
      "available": true,
      "auth": {
        "required": true,
        "authKind": "oauth",
        "signInAvailable": true,
        "authenticated": false,
        "usable": false,
        "authSource": null
      },
      "defaults": {
        "runConfig": {
          "reasoning_effort": "on"
        }
      },
      "capabilities": {
        "runConfig": {
          "reasoning_effort": {
            "supported": true,
            "mode": "toggle",
            "values": ["off", "on"],
            "default": "on"
          }
        }
      },
      "metadata": {
        "api": "responses"
      }
    }
  ],
  "sources": [
    {
      "source": "pi-model-registry",
      "ok": true,
      "count": 1,
      "details": {
        "auth_providers": [],
        "total_model_count": 1
      }
    }
  ]
}
```

## Notes

- `auth.signInAvailable` tells the frontend whether Astro can start provider signin right now
- `auth.authenticated` tells the frontend whether the provider is currently signed in
- `auth.usable` tells the frontend whether the model can execute right now
- Pi-registry models only expose reasoning controls that are explicitly represented in the provider
  model definitions; boolean reasoning support is normalized as `mode: "toggle"`
- `usable: false` does not remove the model from the catalog
- unsupported Pi-registry providers are intentionally filtered out so the catalog only shows the
  providers Astro has actually implemented in the current product surface
