# ADR 0029: Models Tau Does Not List Run From The Provider Control

Status: Accepted — implemented 2026-11-10

Date: 2026-11-10

Relates to:

- [ADR 0027: Custom provider credential hydrate endpoints](./0027-custom-provider-credential-hydrate-endpoints.md).
  The provider control a hydrate answer carries is the authority on what the model may do. This
  ADR extends that authority to a model the runtime's Tau release does not list.

## Context

The runtime pins one tau-ai release and reads only its packaged provider catalog
(`tau_coding.provider_catalog.BUILTIN_PROVIDER_CATALOG`). For a provider in that catalog,
`ProviderFactory.validate_execution` refused any model the catalog did not list with
`Model is not configured for provider <provider>: <model>`. That happened after the session's
credential and provider control had been hydrated and accepted, so a session the platform or a
custom hydrate endpoint had authorised still could not load.

tau-ai keeps the `openai-codex` model list by hand: nine models, `gpt-5.2` to the `gpt-5.6`
family. Release 0.4.10, the latest on 2026-11-10, lists the same nine. The other providers' lists
are generated from models.dev when tau-ai is built. So every model a provider released waited for
a tau-ai release and then a pin bump here. On 2026-11-10 the platform's catalog listed
`gpt-6-astra` for `openai-codex`, and a person's ChatGPT subscription answered five `gpt-6` models
to an ADR 0027 endpoint; none of them could run (issue #134).

For a listed model, Tau's catalog gave membership, the transport, the inputs, the thinking levels
with their per-level mapping, and per-model address overrides. The transports themselves take the
model id per request. A provider Tau does not know already ran from the provider control alone:
its transport from `CUSTOM_API_TRANSPORTS`, its inputs from `CUSTOM_INPUT_KINDS`, its thinking
levels from the projection.

Two facts about context windows, established while deciding. The runtime gives Tau's session a
built provider and no provider settings, so Tau's static `context_windows` are never read, and a
model runs at Tau's default of 128,000 tokens unless its provider discovers its limits. The Codex
transport does discover them, from the account's own model list, for listed and unlisted models
alike.

## Decisions

### 1. An unlisted model of a known provider runs from the provider control

When Tau's catalog knows the provider but does not list the model, the session runs from the
hydrated provider control, as a provider Tau does not know does. The provider control's source is
tdag-django's model-provider catalog, or a custom endpoint that read the provider's own model list
(ADR 0027). It is the authority on what the model may do.

### 2. Transport: the provider's own

The model runs on the provider's Tau transport: `openai-codex-responses` for the `openai-codex`
kind, otherwise the provider entry's `api` or kind. The projected `api` must equal it and be one
the runtime supports; a contradiction is refused as it is for a listed model. Per-model transport
overrides exist only for listed models.

### 3. Inputs: the projection, bounded by the transports

The projected inputs must be non-empty and within `text` and `image`, which every Tau transport
the runtime builds carries. There is no per-model entry to compare them with, so
`validate_input_media` accepts an image for an unlisted model when the projection enables image
input, and refuses it when the projection does not.

### 4. Thinking: the projection, mapped at provider level

The projected thinking levels are the levels the model may run. A selected level outside them
falls back as it does for a provider Tau lacks: to `medium`, else `off`, else the first projected
level, with the same warning. The level is mapped with the provider-level mapping
(`reasoning_effort_for_level` and the provider's thinking parameter). A model with no projected
level runs without a reasoning parameter. Tau's provider-level `thinking_levels` and
`thinking_models` do not bound the projection.

Consequence: for a listed model, Tau's metadata keeps replacing a projected level this release
cannot run (issue #82). For an unlisted model the projected levels reach the provider as they
are, and a level the provider rejects fails the turn at the provider, not the load. A custom
endpoint that reads `codex/models` projects the model's `supported_reasoning_levels`, so its
levels are the provider's own.

### 5. Address and context window

The address is the provider-level `base_url` and headers, with the credential's overrides as
today. The context window is what the provider discovers, else Tau's default; the provider control
gains no field. An optional `context_window` in the projection would serve listed and unlisted
models alike, since Tau's static windows are not read; that is a separate decision.

### 6. Diagnostics

A session whose model is outside Tau's catalog logs `providers.model_outside_tau_catalog` once,
when it loads, with the provider, the model and the session uid, and no credential value.

### 7. Listed models are unchanged

A model Tau lists is validated as before: Tau's metadata bounds the projected inputs and levels.
A model Tau withdrew (`removed_models`) is absent from its list, so it runs from the projection
when the platform still offers it; the platform decides what a provider offers.

## Acceptance criteria

- With a provider control that projects `openai-codex-responses`, `text` and `image` and a set of
  levels, a session on `openai-codex` / `gpt-6.1-sol` validates, builds the Codex provider at the
  provider's address with the projected level, and streams a turn with that model id.
- A listed model behaves exactly as today; the existing factory tests pass unchanged except the
  one that asserted the refusal.
- For an unlisted model, a projected `api` other than the provider's transport, projected inputs
  outside `text` and `image`, empty inputs, and levels on a non-reasoning model are refused; an
  image for a model whose projection lacks `image` is refused.
- The load logs `providers.model_outside_tau_catalog` once for an unlisted model and not for a
  listed one.
- No change to tdag-django or to the provider control schema.

## Rejected

- **Raising the tau-ai pin for each model release.** It depends on another project's release
  cadence; 0.4.10 still lists no `gpt-6` model.
- **tau-ai's `effective_catalog` with a user overlay or a refreshed models.dev cache.** It still
  lags releases, the `openai-codex` list does not come from models.dev, and every runtime would
  need a catalog file of its own.
- **Offering only models Tau lists**, in tdag-django and in custom endpoints. People could not use
  models their own subscription offers.
- **Waiting for huggingface/tau to let a host run unlisted models.** Worth proposing upstream; the
  runtime does not wait for it.
- **Bounding the projection by Tau's provider-level thinking levels.** They describe what Tau's own
  picker offers for the provider, not what a model accepts; the projection comes from the
  provider's catalog or its own model list.
