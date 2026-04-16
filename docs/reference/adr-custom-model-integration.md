# ADR: Available Model Discovery and Session Model Switching

Status: proposed

Date: 2026-04-14

## Context

Astro needs first-class support for custom model providers, but we do not want to overload the main
chat endpoint with provider-specific configuration.

The main request hot path should stay stable:

- `POST /api/chat` remains the path that carries user text to Pi
- the `messages` payload should stay exactly as it is now
- provider discovery and provider configuration should happen outside that path

We also need two concrete capabilities:

1. discover which models are currently available
2. switch a session to a selected model later

The first useful sources are:

- Pi models that are actually available in the current runtime
- models served by Ollama

The important design constraint is extensibility.
We do not want model discovery to become a growing chain of provider-specific `if` cases spread
through the server.

## Decision

This work is split into two parts:

1. available-model discovery
2. session model switching

The discovery part lands first.
The switching part is designed now, but implemented after discovery is in place.

## Part 1: Available Model Discovery

### Goal

Expose a dedicated endpoint called `get_available_models` that returns the models Astro can offer to
the user right now.

In the first iteration, it should:

1. return Pi models that are actually available in the current runtime
2. connect to Ollama and return the available Ollama models

### Endpoint

Recommended endpoint:

- `GET /api/chat/get_available_models`

This endpoint does not send a message to Pi.
It is a control-plane endpoint only.

### Response Shape

Recommended response:

```json
{
  "version": 1,
  "models": [
    {
      "source": "pi-model-registry",
      "provider": "openai",
      "label": "gpt-5.4",
      "model": "gpt-5.4",
      "available": true,
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
    },
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
      "count": 1,
      "details": {
        "host": "http://localhost:11434",
        "listed_model_count": 1,
        "inspected_model_count": 1,
        "inspection_error_count": 0
      }
    }
  ]
}
```

Notes:

- the response should be source-aware so the caller can tell where each model came from
- the response should describe both default run settings and available reasoning levels per model
- the response should be resilient to partial failures
- if Ollama is down, Pi-registry-backed models can still be returned
- if a source fails, that failure should be reported in `sources` without breaking the whole
  response

### Initial Discovery Sources

#### Pi model registry collector

This collector returns the Pi models that are actually available in the current runtime:

- based on Pi `AuthStorage` and `ModelRegistry.getAvailable()`
- includes built-in or custom Pi models only when the provider has usable auth/config

This is not a network discovery call to provider APIs.
It is a runtime-local discovery pass over what Pi can currently use.

#### Ollama collector

This collector resolves the Ollama host and calls the Ollama model-listing endpoint.

Initial host resolution order:

1. backend-provided provider endpoint
2. `OLLAMA_HOST` from the environment

For the first implementation, we expect the environment path to be the one that works.
The backend path is the long-term target.

Discovery call:

- `GET <ollama-host>/api/tags`
- `POST <ollama-host>/api/show` for each listed model

The collector then maps the Ollama response into Astro's common available-model shape.
When the provider only reports binary thinking support, Astro should expose that as a reasoning
toggle instead of inventing discrete reasoning levels.

## Discovery Architecture

Model discovery should be implemented through a collector registry.

Recommended shape:

```ts
interface AvailableModelCollector {
  source: string;
  collect(context: AvailableModelContext): Promise<AvailableModelCollectionResult>;
}
```

Where each collector returns:

- a list of discovered models
- source-level metadata
- source-level errors

Recommended first collectors:

- `PiAvailableModelCollector`
- `OllamaModelCollector`

Recommended aggregator:

- `collectAvailableModels(...)`

The endpoint should call the aggregator, not individual providers directly.

This matters because future additions should look like:

- add a new collector
- register it in one place
- let the aggregator merge results

not:

- edit the endpoint handler
- add one more provider-specific branch
- copy error handling again

## Part 2: Session Model Switching

### Goal

After discovery exists, Astro should be able to switch the active session to one of the returned
models.

This part intentionally depends on Part 1.
The returned available-model records should become the inputs for session switching.

### Hot Path Rule

`POST /api/chat` remains the hot path for user text.

- `messages` stays unchanged
- the chat path should not carry full provider configuration

The chat path may carry a lightweight `model` field.
The provider-specific binding still belongs to the control plane.

### Switching Contract

Recommended endpoint:

- `GET /api/chat/session-model`

This endpoint exposes the selected model already bound to the current session.

Recommended request:

```json
{
  "sessionId": "456"
}
```

The actual session model change happens on `POST /api/chat` with the lightweight `model` field on
the same request as the user message. For sources that require more configuration than just
`provider + model`, the switching layer should resolve that configuration from the source metadata
and backend/environment configuration rather than forcing the frontend to resend everything on each
chat turn.

### Runtime Strategy

We still reject rewriting shared `PI_CODING_AGENT_DIR/models.json` per request.

Instead, switching should use request- or session-scoped provider registration inside the spawned Pi
process.

Preferred direction:

1. Astro stores the selected model binding in session metadata
2. Astro passes request-scoped env vars when spawning Pi
3. an always-loaded runtime extension reads those env vars
4. that extension calls `pi.registerProvider(...)` when needed
5. Pi starts with the correct provider/model without mutating shared runtime files

## Why Split the ADR This Way

The split is intentional:

- discovery is independently useful
- discovery is lower-risk than switching
- the returned model records define the data contract for switching
- it lets us validate the extensibility architecture before we couple it to session state changes

## Shared Runtime State

Astro should still import or mirror host `models.json` into the runtime agent directory for
baseline compatibility.

That is separate from the collector architecture and separate from request-scoped switching.

`models.json` import solves:

- preserving globally configured custom models in containerized Astro

It does not solve:

- dynamic model discovery
- per-session binding
- per-request switching

## Rejected Alternatives

### Put provider discovery into `POST /api/chat`

Rejected because:

- it pollutes the hot path
- it forces provider-specific control data into every chat request
- it makes the main request contract harder to evolve

### Hard-code provider cases inside the endpoint handler

Rejected because:

- every new provider requires editing the endpoint directly
- error handling becomes repetitive
- testing becomes endpoint-centric instead of collector-centric

### Rewrite shared `models.json` for each session

Rejected because:

- concurrent sessions can affect each other
- cleanup is fragile
- shared runtime files should not represent request-local state

## Consequences

Positive:

- `POST /api/chat` stays mostly unchanged
- model discovery becomes independently testable
- adding providers later means adding collectors, not rewriting endpoint logic
- the discovery layer can evolve before session switching lands

Costs:

- one extra control-plane endpoint
- one collector abstraction to define and maintain
- one later follow-up to sync model-binding updates back into already-created backend AgentSession
  records

## Tasks

### Part 1: Discovery

- [x] Add `GET /api/chat/get_available_models`.
- [x] Define a common `AvailableModelCollector` contract.
- [x] Implement `PiAvailableModelCollector`.
- [x] Implement `OllamaModelCollector`.
- [x] Implement a collector registry plus `collectAvailableModels(...)` aggregator.
- [x] Add source-aware response and partial-failure handling.
- [x] Resolve Ollama through backend configuration when available, otherwise `OLLAMA_HOST`.
- [x] Enrich Ollama capability data through `POST /api/show` per discovered model.
- [x] Document the new endpoint in the interface docs.

### Part 2: Switching

- [x] Add a session model inspection endpoint.
- [x] Persist the selected model binding in Astro session metadata.
- [x] Add the runtime extension that maps session-scoped env vars into `pi.registerProvider(...)`.
- [x] Replace fixed backend `llm_provider` and `llm_model` values with selection-aware values for
  new sessions created with an explicit binding.
- [x] Add a lightweight `model` field on `POST /api/chat`.
- [ ] Sync model-binding updates back into already-created backend `AgentSession` records.
