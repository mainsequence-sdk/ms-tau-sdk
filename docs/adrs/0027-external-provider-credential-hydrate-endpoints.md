# ADR 0027: External Provider Credential Hydrate Endpoints

Status: Proposed — not implemented

Date: 2026-11-08

Amends, on acceptance:

- [ADR 0005: Authenticated local development mode](./0005-authenticated-local-development-mode.md).
  Provider credential hydration stays remote in local mode, but it is owned by Main Sequence only
  while no external hydrate endpoint is configured.

## Context

A session's model credential comes from the platform today. The runtime bootstrap answers the
first credential together with the lease, and the runtime hydrates again near expiry by posting
`/api/v1/model-provider-credentials/hydrate/` with its Main Sequence credential. The platform
picks the credential of the session's owner, refreshes an expiring OAuth token before it answers,
and returns a `ProviderControl` projection of what the selected model may do. `ProviderFactory`
builds the provider from that answer and keeps the value in memory only.

An application may serve people the platform does not know. It authenticates them itself, and each
person may connect their own model credential to it, such as a ChatGPT subscription or an
OpenRouter key. The application's rule is that the person's conversations run on that credential,
not on the Organization's. The platform must not grow an owner kind or a credential store for one
application's people. The application keeps the credentials, so the runtime must be able to
hydrate from it.

The hydrate endpoint of tdag-django, the platform backend, is the contract the SDK supports. An
external endpoint speaks the same envelope, so the runtime parses one shape whichever endpoint
answered. tdag-django stays the default and the reference implementation.

## Decisions

### 1. tdag-django by default, an external endpoint when configured

`MAINSEQUENCE_TAU_PROVIDER_CREDENTIAL_HYDRATE_URL` names the hydrate endpoint.

- **Unset or empty:** the runtime hydrates from tdag-django at
  `{MAINSEQUENCE_ENDPOINT}/api/v1/model-provider-credentials/hydrate/`, exactly as today. Nothing
  else in this ADR applies.
- **Set:** every hydration the process makes goes to that URL, in hosted and in local mode, and
  none goes to tdag-django. The setting is process-wide, so it applies to every Agent and session
  the process serves.

The URL is an absolute `https` URL with no user info, query or fragment. Local mode also accepts
`http` to a loopback host, so a project's dev stack can serve it. An invalid URL is a configuration
error at startup that names the setting and never echoes its value.

The runtime calls only hydrate. It never calls `status`, `flush` or `revoke`, so an external
endpoint implements hydrate alone.

### 2. The request

`POST <url>` with `Content-Type: application/json` and the body the runtime sends tdag-django in
the same mode. In hosted mode:

```json
{
  "agent_session_uid": "7d1c2f4e-0b6a-4f0e-9a51-3c2d8e9b1a77",
  "providers": ["openai-codex"],
  "holder_id": "tau-runtime-5f9c",
  "supported_provider_control_schema_versions": [1],
  "execution_selection": {"provider": "openai-codex", "model": "gpt-5.5"}
}
```

| Field | Rule |
| --- | --- |
| `agent_session_uid` | The platform session. Present in hosted mode, absent in local mode. |
| `providers` | Exactly one provider, equal to `execution_selection.provider`. |
| `holder_id` | The runtime's lease holder, for the endpoint's audit. |
| `supported_provider_control_schema_versions` | Always contains `1`. |
| `execution_selection` | `provider` and `model`; in local mode also `thinking_level` when one is selected. |
| `custom_id` | Optional. A configured credential the person chose by name. |
| `organization_environment_uid` | Local mode only, optional. The Environment the developer selected. |

The body never names a person. The endpoint decides whose credential answers from the session, as
tdag-django derives it from the session's owner. It refuses a body that tries to choose the owner,
as tdag-django refuses `created_by_user_uid` and `agent_uid`.

### 3. The answer

`200` with the shape of tdag-django's `ModelProviderCredentialHydrateResponseSerializer`:

```json
{
  "credentials": {
    "openai-codex": {
      "uid": "<the endpoint's stable credential id>",
      "status": "active",
      "credential_kind": "oauth",
      "version": 7,
      "credential_hash": "sha256:...",
      "credential": {
        "type": "oauth",
        "access": "<access token>",
        "expires": 1770000000000
      }
    }
  },
  "missing": [],
  "revoked": [],
  "provider_control": {
    "schema_version": 1,
    "catalog_digest": "sha256:<64 hex digits>",
    "provider": "openai-codex",
    "model": {
      "model": "gpt-5.5",
      "api": "openai-codex-responses",
      "input": ["text", "image"],
      "reasoning": true,
      "thinking_levels": ["low", "medium", "high"]
    }
  }
}
```

- **One entry.** `credentials` holds exactly one entry, keyed by the selected provider. `missing`
  and `revoked` are empty. An endpoint that cannot answer the provider refuses with `409`
  (section 4) and never sends a partial answer.
- **The credential by kind.** `credential.type` equals `credential_kind`:

  | `credential_kind` | `credential` |
  | --- | --- |
  | `api_key` | `{"type": "api_key", "key": "<key>"}`. `api_key` is accepted in place of `key`. |
  | `oauth` | `{"type": "oauth", "access": "<token>", "expires": <epoch milliseconds>, "account_id": "<id>"}`. `account_id` is optional; for `openai-codex` the runtime derives it from the token. |
  | `organization_custom` | `{"type": "organization_custom", "base_url": "<url>", "api": "<api>", "headers": {}, "api_key": "<key>"}`. `api_key` is optional and the runtime validates `base_url` as it does today. |

- **No refresh token.** An external endpoint omits `refresh`. The runtime never reads it, so the
  refresh token stays with the endpoint.
- **Identifiers.** `uid` is the endpoint's stable identifier of the credential it chose. A refresh
  that answers a different `uid` or `organization_environment_uid` is refused, as it is for
  tdag-django. `user_uid` names a Main Sequence User, so an external endpoint omits it. `status`,
  `version` and `credential_hash` are optional non-secret metadata.
- **Catalog definition.** `catalog_definition` (`{"base_url", "compat"}`) is optional, as in
  tdag-django. A runtime whose pinned Tau does not know a built-in provider builds it from that
  definition.
- **Provider control.** In hosted mode `provider_control` is optional: the runtime runs the session
  on the bootstrap's projection, so the platform stays the authority on what the model may do.
  When present, its `provider` and `model` must equal the bootstrap's, or the answer is refused. In
  local mode there is no bootstrap, so `provider_control` is required and must match the local
  selection, as tdag-django's answer must today.

### 4. Refusals

A refusal is `409` with the body of tdag-django's `ProviderControlExecutionErrorSerializer`:

```json
{
  "error_code": "model_provider_credential_unavailable",
  "error_detail": "No active credential is available for the selected provider: openai-codex"
}
```

| `error_code` | When |
| --- | --- |
| `provider_control_contract_unsupported` | Schema version `1` is absent, or `execution_selection` is missing. |
| `provider_control_selection_invalid` | Not exactly one provider, or the selection does not match the session. |
| `model_provider_credential_unavailable` | No current credential for the person, a revoked one, an unknown `custom_id`, or an OAuth token that cannot be refreshed. |

`field_errors` is optional. The runtime reports the code and the truncated `error_detail` as it
reports tdag-django's, and the detail must never contain a credential value. `401` and `403` mean
the endpoint refused the runtime's token. Any other non-2xx answer, or a body that does not parse,
is a hydration failure that ends the load or the turn without a value.

### 5. Authentication

The runtime never sends its Main Sequence credential, a caller assertion, a lease token or any
`X-MainSequence-*` header to an external endpoint. It sends `Authorization: Bearer <token>`, where
the token is `MAINSEQUENCE_TAU_PROVIDER_CREDENTIAL_HYDRATE_TOKEN`.

The token is required when the URL is set, and the runtime fails at startup without it, naming
the setting. It is a secret: it never appears in logs, diagnostics or errors. The token is static,
so a `401` is not retried; it fails the load or the turn with a configuration error naming the
setting. The runtime reads the token at startup, so rotating it takes a restart.

With the default endpoint, authentication is unchanged: the runtime's Main Sequence credential,
refreshed once on `401`.

### 6. What the endpoint guarantees

An external endpoint meets the obligations tdag-django meets:

- It answers only a caller that presents its token.
- It answers only for a session it recorded when it created that session on the platform, and only
  with the credential of the person it bound to that session.
- It refreshes an expiring OAuth token before answering and never answers an expired one.
- It answers a repeated request the same way, because the runtime may retry a hydrate.
- It never logs or echoes a credential value.

The SDK checks the answer's shape, the provider match, the platform's provider control and the
stability of `uid` across refreshes. It cannot check the rest; those are the endpoint's
obligations.

### 7. When the runtime hydrates

- **Session load.** The runtime asks the bootstrap for no credential
  (`provider_credential_source: "external"`) and hydrates from the endpoint before the first turn.
  It builds the provider from that answer and the bootstrap's `provider_control`. A bootstrap that
  still carries a credential means the platform ignored the request, and the load fails rather
  than use it.
- **Near expiry.** The runtime hydrates again from the same endpoint, as it does today.
- **Sessions bound to a platform credential.** A session whose `model_provider_credential_uid` is
  set, as when it was created with a `custom_id`, cannot run on an external endpoint. The binding
  names a credential only tdag-django holds, so the load fails with a configuration error.
- **Local mode.** The runtime sends the local body, without `agent_session_uid`, with the
  endpoint's token. There is no platform session, so the endpoint answers the credential its own
  local configuration names for the developer. Local readiness hydrates from the endpoint too.

### 8. Settings

| Setting | Meaning |
| --- | --- |
| `MAINSEQUENCE_TAU_PROVIDER_CREDENTIAL_HYDRATE_URL` | The hydrate endpoint. Unset or empty means tdag-django. |
| `MAINSEQUENCE_TAU_PROVIDER_CREDENTIAL_HYDRATE_TOKEN` | The bearer token for the endpoint. Required when the URL is set, ignored otherwise. |

The backend timeouts and `MAINSEQUENCE_TAU_BACKEND_MAX_RESPONSE_BYTES` apply to the external
endpoint as they apply to tdag-django.

## Requirements of the platform

1. The bootstrap request accepts `provider_credential_source: "platform" | "external"`, with
   `"platform"` as the default. With `"external"`, the bootstrap skips credential selection, OAuth
   refresh and hydration, binds no platform credential to the session, and answers
   `provider_credentials: null` beside the usual `provider_control`. It still validates the
   selection and refuses an invalid one as today. Withholding a credential grants nothing, so the
   runtime may ask for it without further proof.
2. Session creation keeps binding no credential unless the caller sends `custom_id`. A session for
   a person the platform holds no credential for can then be created and loaded.

## Acceptance criteria

- With the URL unset, every request is byte-for-byte today's, and no hydrate request reaches any
  other host.
- With the URL set, no hydrate request reaches tdag-django, the bootstrap is requested with
  `provider_credential_source: "external"`, and a bootstrap that carries a credential fails the
  load.
- The request body equals the body tdag-django receives in the same mode.
- A stub endpoint that serves tdag-django's documented answer builds `OpenAICodexProvider`. An
  `api_key` answer builds an API-key provider and an `organization_custom` answer builds a custom
  provider, with no change to `ProviderFactory`.
- The external endpoint receives only the configured bearer token, never the Main Sequence
  credential, a caller assertion, a lease token or an `X-MainSequence-*` header.
- A `401` is not retried and fails with a configuration error naming the token setting. A `409`
  surfaces its `error_code` and truncated `error_detail`. A malformed body is a hydration failure.
- In hosted mode, a `provider_control` that differs from the bootstrap's in provider or model is
  refused. In local mode, a missing `provider_control` is refused.
- Near expiry the runtime hydrates again from the endpoint, and a changed `uid` or
  `organization_environment_uid` is refused.
- A session bound to a platform credential fails to load with an external endpoint configured.
- An invalid URL, or a URL without a token, fails startup naming the setting and no value.
- No credential value or token appears in logs, diagnostics, the session transcript or an error.

## Rejected

- **Sending the runtime's Main Sequence credential to the endpoint:** the endpoint could replay it
  against the platform with the runtime's full scope.
- **A different envelope for external endpoints:** two contracts to version and test, and a second
  parser in the runtime. tdag-django's envelope already carries everything a credential needs.
- **A per-Agent source declared in the workflow file:** it needs the platform to resolve and
  authorize the source at session load. The endpoint setting needs only the bootstrap change, and
  a declaration can be added later without changing the envelope.
- **A credential passed with each turn by the caller:** it would appear in the turn request and
  bypass provider control.
- **The platform storing an application's credentials for people it does not know:** it commingles
  one application's people with the platform's users, and makes the platform the store of record
  for credentials of people it did not authenticate.
