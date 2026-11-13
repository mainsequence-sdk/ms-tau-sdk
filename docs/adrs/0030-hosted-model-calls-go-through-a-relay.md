# ADR 0030: Hosted Model Calls Go Through A Relay

Status: Accepted — implemented in the SDK 2026-11-13 for 3.0.0; the platform's relay is built but
not yet deployed

Date: 2026-11-13

Owner decisions of 2026-11-13 that this ADR records:

1. In a hosted session the runtime never receives a model-provider secret. It receives a session
   pass and a relay address, and the relay holds the secret.
2. The change is a breaking release with no backward compatibility and no switch.
3. The SDK keeps its functionality.
4. Local mode keeps receiving the real credential.
5. An application with its own hydrate endpoint follows the same rule and runs its own relay.

The field names, the pass and the relay's routes are defined by the platform's side of this
change. The owner approved the names on 2026-11-13 and the platform built them; section 10 records
what that settled and where the built contract differs from this ADR's draft.

Amends:

- [ADR 0027: Custom provider credential hydrate endpoints](./0027-custom-provider-credential-hydrate-endpoints.md).
  Section 3, the answer: in a hosted session a custom endpoint answers a pass and a relay address,
  never a secret. "Requirements of the platform" is no longer "none".
- [ADR 0029: Models Tau does not list run from the provider control](./0029-models-tau-does-not-list-run-from-the-provider-control.md).
  Section 5, the address: in a hosted session the address is the relay's.

## Context

A session's model credential comes from the platform at bootstrap, and again from hydration inside
the run. Today that answer is the real secret: an API key, or a sign-in's access token and account
id. `ProviderFactory` builds the Tau provider from it and the runtime calls the provider directly.

The runtime is one process. It holds the Agent's own code, its tools and their dependencies beside
the SDK and Tau. The Security and access guide already says that this process holds the provider
credentials it was given. Any code in it can read a secret from memory and use it somewhere else.
A copied secret works from any machine, directly at the provider, until its owner rotates it. The
platform never sees those calls and cannot stop them.

Three narrower faults on the same path were closed on 2026-11-13: a saved credential that carried
an address, a runtime that could overwrite a credential, and a hydrated credential that moved a
provider Tau knows (2.0.34, issue 138). What is left is the secret itself.

What the SDK and Tau 0.4.2 do today, which shapes the decisions:

| Fact | Consequence |
| --- | --- |
| All six transports take their address from one variable in `ProviderFactory.build` | Every transport can be pointed at a relay with one change |
| All six are HTTPS requests with a streamed answer | A relay only has to forward requests and stream answers |
| `ProviderControl` forbids extra fields and accepts only `schema_version` 1 | The relay address cannot travel in the provider control |
| An address from outside Tau's catalog must pass the prohibited-host checks | The relay address passes those checks, like any other |
| Only the Codex transport asks for the credential again during a run | The pass must stay valid for the whole run |
| For `openai-codex`, a missing account id is read out of the access token | A relayed answer must carry the account id, because a pass is not a token |
| The Google transport sends the secret as the `key` query parameter | The pass reaches the relay in a URL for Google |

## Decisions

### 1. In a hosted session the runtime holds a pass, not a secret

A hosted runtime receives, for its session's provider:

- a **session pass**, in the place where the credential kind carries the secret today;
- a **relay address**, in a server-owned field beside `credential`.

It sends every model call to the relay address with the pass. The relay checks the pass, puts the
real secret into the request, forwards it to the provider's own address and streams the answer
back. The provider's protocol is forwarded unchanged.

A pass is worth nothing outside the relay, and nothing after the session's run ends.

### 2. The relayed answer

The answer keeps the shape of today's hydrate answer. `provider_control` is unchanged. One entry
changes:

```json
{
  "uid": "<credential id>",
  "credential_kind": "api_key",
  "credential": {"type": "api_key", "key": "<session pass>"},
  "relay_definition": {"base_url": "https://<relay>/model-relay"}
}
```

- For `oauth`, the pass is in `access`, and `account_id` is present for `openai-codex`. There is no
  `refresh` and no `expires`.
- For `organization_custom`, the pass is in `api_key`, beside `api`. The entry carries no
  `base_url` and no `headers`: the provider's address and headers stay with whoever serves the
  relay.
- `catalog_definition`, when present, still gives the `compat` settings. Its `base_url` is not
  used for a relayed entry.
- `uid`, `organization_environment_uid` and the refresh rule that compares them are unchanged.

### 3. The runtime announces relay support, and a hosted answer without a relay is refused

- The runtime sends `supported_model_relay_versions: [1]` in its bootstrap and hydrate requests,
  where it sends `supported_provider_control_schema_versions` today.
- In a hosted session the SDK refuses a credential entry that has no `relay_definition`, with a
  configuration error that names the field and never a value. It does not fall back to calling
  the provider with what it received. It also refuses a relayed entry that carries `base_url` or
  `headers`, and a refresh that answers a different `relay_definition`.
- A runtime that did not announce the relay refuses an entry that carries `relay_definition`. A
  request announces the relay exactly when it expects a relayed answer.
- The platform refuses a runtime that does not announce support. So there is no mixed state: an
  older SDK cannot start a hosted session, and a newer SDK cannot be handed a secret.

### 4. How a provider is built from a relayed entry

`ProviderFactory.build` builds the same Tau provider object it builds today. Two inputs change:

- the address is `relay_definition.base_url`, after the existing prohibited-host checks;
- the secret is the pass.

Headers come from Tau's catalog only. Tau turns OpenAI prompt-cache keys, and session-affinity
headers on the Responses API, on by default only for OpenAI's own host; behind the relay it sees
the relay's host, so for a provider whose own address is OpenAI's the SDK states those two
defaults in `compat`, and the provider receives what it would without the relay. Nothing else in the build changes: the transport is chosen
from the provider control as today, and thinking, inputs, timeouts and `compat` are applied as
today. Each transport appends its own path to the base it is given, so one relay base serves all
six.

The Codex transport keeps asking for the credential before each request. In a relayed session
that returns the same pass, and re-hydrates only if the platform answers an expiry.

### 5. Functionality that stays

Streaming, tool calls, thinking levels, image input, model discovery, retries, provider error
handling, all six transports, models Tau does not list (ADR 0029), providers Tau does not know,
Organization custom providers, session selection, and credential refresh for Codex.

A provider's error answer reaches the runtime unchanged through the relay, so the existing error
parsing applies. A refusal by the relay itself is a `403` with a `detail`; Tau reports it as a
provider failure and does not retry it, as it retries no `403`. An unreachable provider is a
`502`, which Tau retries like any `5xx`.

### 6. Local mode is unchanged

In local mode the runtime is the developer's own machine and the credential is one that person may
already use. Hydration answers the real credential, and the SDK builds the provider from it as
today. This is the only place the SDK still builds a provider from a real secret.

Local mode does not announce relay support and does not require `relay_definition`.

### 7. Custom hydrate endpoints answer a relayed entry too

An application with its own hydrate endpoint (ADR 0027) has the same exposure: its endpoint hands
a person's secret to a hosted runtime. So:

- In a hosted session a custom endpoint answers the entry of section 2: its own pass and its own
  relay address. The contract stays one contract.
- The application runs the relay, because only it holds those secrets.
- `_custom_provider_evidence` refuses a hosted answer that has no `relay_definition`, as section 3
  says for the platform.
- In local mode, with a release or with a local address, a custom endpoint may still answer the
  real credential.

An application must have its relay running before it upgrades to the breaking release.

### 8. The SDK ships the relay as a component

The relay is the same code for every application, and it is the code where a mistake exposes
secrets. The SDK ships it once, as `ms_tau_sdk.model_relay.create_model_relay_router`, a FastAPI
router the application includes under the address its hydrate endpoint answers.

An application supplies two things:

1. **Resolve a pass** (`resolve_pass`): the session it names, or `RelayRefused`.
2. **The provider call** (`resolve_upstream`): for that session, a `RelayUpstream` with the
   session's wire protocol, the provider's address, the secret and, for Codex, the account id; or
   `RelayRefused`.

The draft had the application also list the paths of each provider it serves. The paths belong to
Tau's transports, not to the application, so they are fixed in the SDK (`RELAY_PATHS`), the same
as the platform's, and the application only names the session's wire protocol.

The component does the rest, and none of it is configurable:

- it accepts the pass in the place each transport puts the secret (a bearer token, `x-api-key` or
  the `key` query parameter), removes it, and puts the secret back in that place;
- it forwards the method, the path under the provider's base, the query without `key`, the body and
  the request headers without hop-by-hop headers, `Cookie`, `Forwarded`, `X-Real-IP` and
  `X-Forwarded-*`. A leading `/v1` in the path is the transport's own and is dropped when the
  provider's address already ends in `/v1`;
- it takes the upstream address only from the application's answer to step 2, never from the
  request, and returns a redirect without following it;
- it refuses a path the session's transport does not use, with `403` and a `detail`; an
  unreachable provider is `502`;
- it returns the provider's status, headers and body as they arrive, streamed;
- it reads the body whole and sets no size limit, because a call with images is larger than any
  framework default; the server in front of it sets the limit;
- it logs no pass, no secret, no request URL and no path, and keeps httpx's own request line,
  which holds the URL, out of the logs while it calls a provider.

The platform's own relay is not part of the SDK and is not described here. The two must behave
the same towards a runtime, and the contract tests of section 9 are what holds them together.

### 9. One breaking release

- The release is `3.0.0`. A dependency range such as `>=2.0.32,<3` therefore does not pick it up
  by accident.
- It contains sections 1 to 8 together. There is no setting that turns the relay off and no
  release in between that accepts both answers in a hosted session.
- The Security and access guide, the runtime contract, the settings page, the troubleshooting page
  and the compatibility page change in the same release. The guide's statement that the process
  holds provider credentials becomes true for local mode only.
- The changelog names the upgrade order: the platform's relay first, then applications with their
  own endpoint ship their relay, then runtimes upgrade.
- The SDK's log sanitizer redacts a `key` query value in every log line, so the httpx request line
  of a Google call never carries the pass, or in local mode the key.

### 10. Settled with the platform's contract

Approved by the owner on 2026-11-13 and built by the platform as this ADR uses them:

1. `relay_definition`, holding `{"base_url": ...}`, beside `credential`.
2. The request field `supported_model_relay_versions`, a list that includes `1`, sent where
   `supported_provider_control_schema_versions` is sent.
3. The pass is an opaque string to the SDK.
4. The relay's path prefix is `/model-relay`, with one route for every path below it.

Where the built contract differs from, or adds to, the draft:

- The `organization_custom` pass is in `api_key` (section 2); the draft did not name the field.
- The platform's refusals of a runtime that does not announce the relay: the bootstrap answers
  `tau_runtime_version_unsupported` with a field error on `supported_model_relay_versions`, and a
  hydration answers `409` `provider_control_contract_unsupported`. While no relay is deployed, a
  hosted hydration answers `409` `model_provider_credential_unavailable`. A person's own
  hydration is never relayed.
- The relay returns a redirect without following it; the draft said it follows none to another
  host.
- The relay reads the request body with no framework cap (section 8).

Decided in this repository:

5. The component is a FastAPI router. An application that wants it as its own ASGI application
   includes the router in an empty `FastAPI()`.
6. A custom endpoint's relay may run in a different release from its hydrate endpoint. Its
   address passes the same prohibited-host checks as the platform's relay, so a relay on a
   loopback, link-local or metadata address is refused either way.

## Consequences

- An Agent's code cannot take a provider secret out of a hosted session.
- Every hosted model call depends on a relay. When it is down, the runtime gets a connection
  error or a `5xx`, and the existing retry rules apply; the turn fails if they are exhausted.
- One more hop on every model call. It is not measured.
- The SDK gains a server-side component it must maintain and keep in step with the platform's
  relay.
- An application with its own endpoint cannot take `3.0.0` until it runs a relay. One such
  application is known.
- An application's relay takes the provider's address from the application and calls it as
  given. It does not resolve the address and refuse a private one, as a relay that calls
  addresses chosen by other people would have to: the application chooses its own addresses.
- For Google the pass travels in the request URL, so a relay and everything in front of it must
  not log URLs. The component of section 8 enforces that for itself only.

## Requirements of the platform

- It answers the entry of section 2 to a runtime that announces relay support, and refuses a
  runtime that does not.
- It serves a relay that behaves as section 8 describes towards a runtime.
- It keeps answering the real credential to a person's own hydration in local mode.

## Acceptance criteria

- A hosted session load with a relayed entry builds each of the six transports with the relay
  address and the pass, and with no credential address or header.
- A hosted session load refuses an entry without `relay_definition`, from the platform and from a
  custom endpoint, and makes no provider call.
- The bootstrap and hydrate requests carry `supported_model_relay_versions`.
- A relayed `openai-codex` entry without `account_id` is refused; the SDK does not try to read an
  account id out of a pass.
- A relay address that the prohibited-host checks refuse is refused.
- Local mode loads a session from a real credential, with and without a custom endpoint, and
  sends no relay announcement.
- The relay component, against a fake provider, for each transport: forwards a streamed answer, a
  tool call and an error answer unchanged; refuses an unknown pass, another provider's path and a
  path outside the transport's; takes the address only from the application; follows no redirect;
  and writes no pass, secret or URL to a log.
- One contract test suite runs against the component and is published for the platform's relay to
  run.
- No test, log line, `repr()` or error message contains a pass or a secret.

## Implementation

Implemented in the SDK for 3.0.0:

- `backend/client.py` announces the relay in hosted hydrate requests and, through
  `provider_credential_from_hydration(relayed=...)`, accepts only a relayed entry from a request
  that announced it and none from one that did not, for the platform and a custom endpoint alike;
  `runtime/manager.py` announces it in the bootstrap request.
- `providers/factory.py` builds every transport on `relay_definition.base_url` and the pass.
- `model_relay/` is the component of section 8, and `model_relay/contract.py` the published
  contract cases.

The acceptance criteria are covered by `tests/unit/test_hosted_model_relay.py` (every transport
through a fake relay and end to end through the component, refusals, local mode, Codex),
`tests/unit/test_model_relay_component.py` (every contract case and the component's own rules),
`tests/unit/test_custom_hydrate_endpoints.py` (custom endpoints in hosted and local mode) and
`tests/unit/test_logging.py` (a `key` in a logged URL). Nothing was run against a deployed relay.

## Rejected

- **Keep accepting a real secret in hosted sessions for a while.** It keeps the path that hands
  out the secret alive, and an older runtime given a pass would send it to the provider's own
  address.
- **Put the relay address in `catalog_definition` or mark the entry `organization_custom`.** Both
  would work with no SDK change. Both give an existing field a second meaning, and neither lets the
  SDK refuse an answer that should have been relayed.
- **A helper on `localhost` in the runtime's workload.** The prohibited-host checks refuse
  loopback, and the secret would sit in the same workload as the Agent's code.
- **One normalised API in place of the provider protocols.** The SDK would give up Tau's own
  transports, and tool calls, thinking and streaming would have to be rebuilt for every provider.
- **Let each application write its own relay.** The part where a mistake exposes secrets would be
  written once per application.
- **Stop reading the refresh token only.** The SDK never read it. The exposure is the secret the
  runtime does use.
