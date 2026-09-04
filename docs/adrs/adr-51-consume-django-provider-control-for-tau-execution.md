# ADR 51: Consume Django Provider Control for Tau Execution

Status: Implemented

Date: 2026-08-31

Related decision: `tdag-django/docs/agents/adr/adr-024-django-owned-model-provider-control-plane.md`

Amended: 2026-09-04 by
`tdag-django/docs/agents/adr/adr-029-organization-owned-custom-model-providers.md`.
Organization-owned custom providers reuse provider-control schema version
`1`, the existing bootstrap and hydration routes, and Astro's installed
OpenAI-compatible constructor. Astro still has no custom registry or runtime
registration API.

Supersedes:

- `docs/reference/adr-interactive-provider-signin.md`; and
- the Astro-owned `PATCH /api/chat/session-config` contract in
  `docs/reference/adr-editable-session-config.md`.

## Context

Django now owns the Main Sequence provider catalog, product enablement,
credential status, durable OAuth and device-flow attempts, token exchange and
refresh, and canonical credential persistence. Command Center and MCP consume
those Django contracts directly; they no longer need an AgentSession or an
Astro deployment to discover models or sign in.

Astro remains responsible for execution. Its current Tau bootstrap already
provides the correct foundation:

- `POST /api/v1/agent-sessions/{uid}/tau-runtime/bootstrap/` returns the
  Django-selected provider, model, thinking level, and an exact
  session-authorized credential envelope in one consistency boundary;
- `ProviderFactory` constructs the Tau inference provider in memory; and
- expiring OpenAI Codex credentials are rehydrated from Django rather than
  persisted locally.

However, Astro still exposes a catalog assembled from Tau's
`BUILTIN_PROVIDER_CATALOG` and owns process-local sign-in attempts backed by
Tau OAuth adapters. `ProviderFactory` also treats Tau's provider/model list as
both a product catalog and an execution registry. These are obsolete control-
plane responsibilities and make a pod restart destructive to sign-in state.

The Django provider-control package is deliberately installed only in the
Django repository and deployment image. Installing it in Astro would create a
cross-project Python dependency in the wrong direction.

## Decision

### Ownership boundary

Django is the only authority for:

- the published provider/model catalog and its digest;
- known, enabled, authenticated, and selectable state;
- provider sign-in, callback, device polling, exchange, refresh, revoke, and
  durable attempt state; and
- credential ownership, encryption, versioning, and audit.

Astro is the execution consumer and continues to own:

- the Tau agent loop, tools, streaming, and session runtime;
- exact bootstrap validation;
- construction of in-memory Tau inference providers;
- runtime credential hydration or rehydration for the authorized session; and
- safe execution-error translation and secret redaction.

Astro must not install or import `tau-provider-control`, Django application
code, or any other package from the Django repository. The integration is
authenticated HTTP only.

### Product catalog versus execution registry

Tau's built-in provider catalog remains available inside Astro only as the
execution-capability registry for built-in providers. It can supply transport
details that belong to the engine, including API adapters, base URLs,
compatibility flags, reasoning parameter mappings, and provider-specific
headers.

It must not be returned as the Main Sequence catalog or used to decide what
the product publishes. For a built-in provider, appearance in Tau means only
that this Astro release knows how to execute it, while appearance in Django
means the platform may select it; execution requires both statements to be
true. For an Organization custom provider, exact Django provider-control
evidence and exact auth hydration replace built-in catalog membership. Astro
accepts only its fixed installed `openai-completions` and
`openai-responses` transports, requires an explicit hydrated base URL, and
loads no configurable adapter code.

Astro will not add a local JSON/TOML copy of Django's catalog, synchronize a
catalog at process startup, or read `~/.tau/catalog.toml` in managed runtime
mode.

### Remove Astro's custom-provider registry

Astro removes its unused custom-provider registration machinery. The cutover
deletes `CustomProviderBuilder`, `ProviderFactory._custom`,
`ProviderFactory.register`, the custom-provider branches in selection and
construction, and the corresponding test-only registration coverage.

After cutover, Astro has no custom-provider registry, builder hook, runtime
registration API, or provider-specific execution bypass. `ProviderFactory`
uses pinned Tau implementations after validating Django's exact projection.
Built-ins must also match Tau's pinned built-in registry. Organization custom
providers take one constrained branch through the already installed
`OpenAICompatibleProvider`; they cannot register or select executable code.

### Bootstrap contract

Execution continues through the existing Tau bootstrap route. Django will add
one required `provider_control` object to that response. The request declares
the exact provider-control schema understood by the runtime:

```json
{
  "holder_id": "astro-holder-id",
  "ttl_seconds": 300,
  "bootstrap_request_uid": "<request-uid>",
  "history_after_sequence": null,
  "known_capability_hashes": [],
  "supported_snapshot_schema_versions": [1],
  "tau_runtime_version": "<astro-version>",
  "supported_provider_control_schema_versions": [1]
}
```

Django requires schema version `1` to appear in
`supported_provider_control_schema_versions` and validates this requirement
before acquiring a runtime lease. A missing field or a list with no supported
version fails with `provider_control_contract_unsupported`. Django advertises
`tau_runtime_bootstrap = "v2"` in `runtime_capabilities` for this contract.

The successful response contains:

```json
{
  "provider_control": {
    "schema_version": 1,
    "catalog_digest": "sha256:<canonical-digest>",
    "provider": "openai-codex",
    "model": {
      "model": "gpt-5-codex",
      "api": "openai-codex",
      "input": ["text", "image"],
      "reasoning": true,
      "thinking_levels": ["low", "medium", "high", "xhigh"]
    }
  }
}
```

This is an execution projection of the already selected Django catalog entry,
not a second catalog endpoint. It contains no token, client secret, internal
header, price, Tau compatibility map, or filesystem configuration. The
existing `session` and `provider_credentials` fields remain unchanged.

This is an intentionally breaking Tau bootstrap v2 contract. Django rejects
v1 and undeclared provider-control clients after cutover. Astro requires the
object during response parsing and rejects a response that omits it, reports a
different bootstrap capability, or uses an unsupported provider-control
schema. The public user catalog schema is unchanged.

ADR 51 Astro removes the `tau_runtime_contract` compatibility selector and the
`adr48` and bootstrap-v1 loading paths. There is one managed Tau runtime
contract after cutover: bootstrap v2 with provider-control schema 1. No
optional field, permissive fallback, feature flag, automatic detection, or
mixed-version mode exists.

### Agent-targeted sessionless execution

Agent-targeted sessionless responses do not create an `AgentSession` and do
not call the Tau bootstrap route. They already hydrate the exact provider
credential through Django using `agent_uid`. This path will reuse that same
canonical hydration operation rather than adding another endpoint or network
round trip.

For sessionless execution, Astro adds required `execution_selection` and
`supported_provider_control_schema_versions` fields to the existing
credential-hydration request:

```json
{
  "agent_uid": "<agent-uid>",
  "holder_id": "agent-response/<run-uid>",
  "providers": ["openai"],
  "supported_provider_control_schema_versions": [1],
  "execution_selection": {
    "provider": "openai",
    "model": "gpt-5.4"
  }
}
```

For Tau `agent_uid` or `agent_session_uid` execution hydration, Django requires
both fields, validates that provider-control schema 1 is supported, verifies
that the selected provider is one of the providers being hydrated, and checks
that the exact provider/model is known and enabled in Django's canonical
catalog. Django returns the active credential and the same typed
`provider_control` execution projection used by Tau bootstrap. Missing
contract evidence or credentials, disabled or unknown selections, and a
provider mismatch fail the hydration operation without returning partial
credential material.

Credential-only Tau execution hydration is removed at cutover. This applies
to initial agent-targeted execution and later session or agent credential
rehydration. Non-Tau credential workflows are outside this execution contract.
Astro requires the returned projection before constructing or refreshing a
provider.

Django uses one internal projection builder for bootstrap and credential
hydration so the schema, digest, catalog lookup, and enablement rules cannot
drift. Astro resolves request overrides and agent defaults first, sends the
effective provider/model selection, and then intersects the returned Django
projection with Tau's execution registry. Credential hydration proves exact
Agent/User authorization; the projection proves product selection. Neither
statement substitutes for the other.

### Canonical session selection and mutation

Django's `AgentSession.llm_provider`, `AgentSession.llm_model`, and
`AgentSession.llm_thinking` fields are the canonical durable session
selection. Creation and every later selection mutation go through Django's
AgentSession domain service and DRF contract. Django resolves partial updates
against the locked current session, validates the resulting selection against
its canonical catalog and product policy, and persists the complete selection
atomically. Unknown, disabled, or contradictory selections are rejected
without changing the session.

Astro removes `PATCH /api/chat/session-config` in the ADR 51 cutover. It does
not retain a compatibility facade, proxy, redirect, or second mutation
contract. Callers that are allowed to change an AgentSession use the canonical
Django operation directly. Astro's Tau registry remains an execution check at
bootstrap and cannot authorize or persist a session selection.

A loaded Astro session holds an in-memory provider and must not continue after
its canonical selection changes. The caller nevertheless performs exactly one
Django mutation request; it does not cancel the session, wait for lease
release, poll, or retry a second mutation request.

Django handles an idle loaded runtime inside the same transaction that changes
the selection. It locks the AgentSession and runtime state, validates and
persists the complete canonical selection, invalidates the idle
`runtime_run` lease, and returns the resulting provider, model, thinking level,
and catalog digest. Astro observes the invalid lease during renewal and evicts
the cached provider.

If a new turn races with invalidation, Astro's required pre-provider
`begin_turn` operation rejects the stale lease before any inference request is
sent. Astro then evicts the stale runtime, performs one cold bootstrap v2, and
retries `begin_turn` internally. The user does not resubmit the model change or
the prompt.

Django rejects a selection mutation with a typed conflict only while the
session is actively `working` or `persisting`; it never changes the selection
under an in-flight model or durability operation. Product clients disable
model selection while a turn is active. Loading or idle runtime ownership is
not exposed as a caller-managed cancellation workflow.

This is an intentional breaking removal. The coordinated rollout migrates
all callers to Django before deploying the ADR 51 Astro release. There is no
mixed route-ownership window.

### Fail-closed execution validation

Before constructing a Tau provider, Astro validates all of the following:

1. the provider-control schema is supported and its digest is well formed;
2. the projected provider and model exactly match the session selection;
3. the credential envelope is for that exact provider;
4. a built-in selection exists in Tau's execution registry, or a custom
   selection is explicitly marked `organization_custom`, uses one of the two
   fixed OpenAI-compatible transports, and has an explicit hydrated base URL;
5. Tau supports the selected `api` transport; and
6. the selected thinking level and requested input media are allowed by both
   the Django projection and the Tau engine.

The Django projection limits execution; Tau metadata cannot broaden it.
Missing or contradictory evidence fails runtime loading before the agent loop
starts. Safe error codes distinguish an invalid bootstrap from a model that
this Astro release cannot execute, without returning credentials or internal
headers.

The catalog digest is logged as safe provenance and attached to runtime
diagnostics. It is not compared with a digest of Tau's catalog because the two
artifacts have different owners and schemas.

### Credential handling

Astro uses only the credential returned by bootstrap or by exact
`agent_session_uid`/`agent_uid` hydration. Credentials remain in memory and
are never written to an Astro file, environment overlay, database, session
entry, snapshot, log, or error payload.

Organization custom credentials use the same envelope and may contain an API
key, explicit headers, both, or neither. When no API key is present, Astro
omits its generated bearer header; an explicit `Authorization` header remains
authoritative. The envelope has no User-credential status, revision, or digest.

When a credential is near expiry, Astro rehydrates through Django. Django owns
refresh and returns the current canonical envelope. Astro must not refresh a
token with Tau OAuth code or flush it back. The ADR 51 Astro runtime removes
its provider-credential flush client and never uses Django's generic flush
operation. Any Django flush contract retained for Pi or other non-Tau callers
is outside the Tau execution path and does not provide compatibility for an
older Astro runtime.

Astro does not blindly replay an inference request after an authentication
failure if the provider may already have accepted or streamed it. It obtains a
fresh credential for the next safe operation and surfaces a redacted error for
the ambiguous one.

### Removal of Astro control-plane routes

ADR 51 approves a hard removal of Astro's model-provider control-plane
surface. The Astro cutover release unregisters all of the following routes:

- `GET /api/chat/get_available_models`;
- `GET /api/models/catalog`;
- `GET /api/model-providers`;
- `POST /api/model-providers/{provider}/signin`;
- `GET /api/model-providers/{provider}/signin/{attempt_id}`;
- `POST /api/model-providers/{provider}/signin/{attempt_id}/manual`;
- `POST /api/model-providers/{provider}/signin/{attempt_id}/cancel`; and
- `POST /api/model-providers/{provider}/signoff`.

There is no Astro proxy, redirect, compatibility facade, frozen local
implementation, or unavailable stub. After cutover, requests to those
unregistered paths receive Astro's ordinary `404`. Catalog, sign-in start,
attempt retrieval and cancellation, credential enrollment or revoke, and
credential status callers use Django's canonical contracts directly.

The same Astro release deletes `providers/catalog.py`,
`providers/signin.py`, `ProviderSignInManager`, their application and
dependency wiring, and Tau OAuth imports that are not used by execution. It
also removes the corresponding route, manager, catalog, and sign-in tests and
updates the public interface documentation. Runtime credential hydration and
`ProviderFactory` remain because they are execution responsibilities.

This is an intentional breaking public-contract removal approved by this ADR.
Every caller must migrate to Django before the Astro release is deployed; no
traffic-observation window or later route-removal approval remains.

## Execution Flow

```text
Django AgentSession selection
        |
        v
tau-runtime/bootstrap
  session + provider_control + exact credential
        |
        v
Astro validates Django selection against Tau engine capability
        |
        v
ProviderFactory builds an in-memory Tau provider
        |
        v
Tau executes and streams the turn
        |
        +---- credential near expiry ----> Django rehydrate/refresh
```

Catalog browsing and sign-in do not enter this flow and never launch Astro.

Agent-targeted sessionless execution uses the parallel existing path:

```text
Astro resolves the effective agent default or request override
        |
        v
credential hydrate by exact agent_uid + execution_selection
  provider_control + exact credential
        |
        v
Astro validates Django selection against Tau engine capability
        |
        v
Tau executes the one-shot response
```

It does not create an `AgentSession`, fetch the general user catalog, or add a
second backend request.

### UX and request budget

Provider control must not add request amplification or repeated interactive
authentication. The steady-state budget is:

| User operation | Django interaction |
| --- | --- |
| Open or explicitly refresh the model selector | One direct canonical catalog request. |
| Change an idle AgentSession selection | One canonical Django mutation request that also invalidates the idle runtime lease. |
| Load or resume a cold AgentSession | One bootstrap v2 request containing session state, provider-control evidence, and the exact credential. |
| Run a warm AgentSession turn | No catalog, selection, or credential request. |
| Run one agent-targeted sessionless response | One existing exact `agent_uid` hydration request containing provider-control evidence and the credential. |
| Refresh a near-expiry runtime credential | One background rehydration only when required; no User interaction. |

Astro never calls the general catalog during execution. Provider-control
validation adds fields to bootstrap or hydration responses, not another
network exchange. Successful interactive sign-in is durable in Django;
ordinary execution never asks the User to sign in again. A stale-lease reload
caused by a model change is handled inside Astro before provider execution and
does not surface as a second UX operation.

## Minimal Implementation

The Astro change is intentionally bounded:

1. Add typed provider-control request and response models and require
   bootstrap capability v2 with provider-control schema 1.
2. Add one validator at the boundary between bootstrap parsing and
   `ProviderFactory`.
3. Make selection, thinking, and media validation intersect Django's
   projection with Tau execution metadata.
4. Require `execution_selection`, supported provider-control versions, and a
   returned `provider_control` object for every Tau session or agent credential
   hydration.
5. Move session-selection mutation exclusively to Django, enforce the
   single-request active-turn/idle-lease rules, and remove Astro's
   `PATCH /api/chat/session-config` route and request model.
6. Remove `ASTRO_TAU_RUNTIME_CONTRACT`, bootstrap v1, and the `adr48`
   multi-request loading path; retain only bootstrap v2.
7. Remove Astro's unused `ProviderFactory.register` method, custom-builder
   type and map, custom branches, and tests. Astro has no custom-provider
   extension point after cutover.
8. Preserve the existing in-memory provider construction and exact credential
   rehydration path.
9. Add focused unit and contract tests for valid execution, mismatched
   provider/model/API, unsupported schema/transport, missing credentials,
   disabled or unknown sessionless selections, thinking/media narrowing, and
   redaction.
10. Remove Astro's catalog, provider-control routes, process-local sign-in
   manager, and unused OAuth imports in the same cutover release.

The first execution slice maps directly onto existing code:

| File | Change |
| --- | --- |
| `src/astro/backend/models.py` | Type `provider_control` and add it to `TauRuntimeBootstrap`. |
| `src/astro/providers/factory.py` | Validate the Django projection against Tau execution metadata before building the provider and remove the untyped custom-builder registry. |
| `src/astro/runtime/manager.py` | Require bootstrap v2, pass bootstrap evidence into the factory, reload once on a stale pre-provider lease, record only safe digest/version diagnostics, and remove legacy loading paths. |
| `src/astro/settings.py` | Remove the runtime-contract selector; ADR 51 has one supported managed contract. |
| `src/astro/backend/client.py` | Send required provider-control schema support and exact execution selection on Tau hydration. |
| `src/astro/api/responses.py` | Send the effective sessionless provider/model through existing credential hydration and require its returned projection. |
| `src/astro/api/sessions.py` | Remove the session-config mutation route; retain only execution/session operations that Astro owns. |
| `src/astro/api/providers.py` | Remove the Astro catalog, status, sign-in, attempt, cancel, and signoff routes. |
| `src/astro/app.py` and `src/astro/api/dependencies.py` | Remove provider sign-in manager construction, lifecycle, and dependency wiring. |
| `src/astro/providers/catalog.py` and `src/astro/providers/signin.py` | Delete the obsolete Astro control-plane implementations. |
| `tests/unit/test_runtime_manager_adr49.py` | Cover bootstrap acceptance and fail-closed rejection. |
| `tests/unit/test_provider_credentials.py` | Cover execution compatibility, credential matching, and redaction. |
| `tests/unit/test_llm_endpoint.py` | Cover sessionless defaults, overrides, disabled/unknown selections, and projection narrowing. |

The coordinated Django slice makes bootstrap v2 and Tau execution hydration
strict, extends their serializers and services, and reuses one projection
builder. It rejects old or undeclared Tau runtime contracts before releasing
credentials or acquiring a bootstrap lease and adds focused serializer,
service, schema, and contract tests. No new Django route, database model, or
provider-control client is introduced.

No new backend network round trip is needed: session-backed evidence and
credentials arrive in the existing bootstrap response, while sessionless
evidence and credentials arrive in its existing hydration response.

This decision does not add a provider-control client package, a new Astro
service, a database model, a worker, an OAuth state machine, a filesystem
store, a local catalog artifact, a second provider factory, or a generic
plugin framework.

## Rollout

This is a coordinated breaking cutover with a planned execution outage. It is
not an additive or mixed-version rollout.

1. Build and validate the Django and Astro artifacts together. Django provides
   strict bootstrap v2 and hydration contracts; Astro supports only those
   contracts.
2. Migrate every session-selection caller and every Astro catalog, status,
   sign-in, attempt, cancel, enrollment, revoke, and signoff caller to the
   canonical Django contracts.
3. Drain and stop all Astro Tau execution and prevent new turns from starting.
4. Deploy Django with bootstrap v2 enforcement, strict Tau hydration,
   catalog-aware single-request AgentSession mutation, atomic idle-lease
   invalidation, and active-turn conflict enforcement.
5. Reconcile or redeploy every Astro Tau runtime onto the ADR 51 release. Old
   runtime instances and images are unsupported and Django rejects them.
6. Reopen execution traffic only after the new runtime passes bootstrap v2.
   Existing durable AgentSession rows may resume through the new runtime; an
   existing runtime instance may not continue.
7. Observe bootstrap validation failures by safe code, provider, model, Astro
   version, and catalog digest; never log credential material.

There is no independent Django or Astro rollback to a pre-ADR contract. A
pre-ADR binary or image is not a valid target. Recovery uses a forward fix or
a coordinated patched build that preserves bootstrap v2, strict hydration,
canonical Django ownership, and all Astro route removals.

## Consequences

Model discovery and sign-in remain available when no Astro runtime exists.
Django owns one durable product contract, while Astro keeps the Tau-specific
knowledge required to execute it. A newly published Django model cannot run on
an older Astro release unless that Tau engine explicitly supports it, and the
failure occurs before agent execution with safe diagnostics.

The cost is a required bootstrap and hydration projection, removal of the
legacy loading paths, and a coordinated execution outage while every runtime
is redeployed. This is preferable to supporting mixed runtime contracts,
installing Django's provider-control package in Astro, duplicating the catalog,
or moving Tau execution internals into Django's public catalog.

## Rejected Alternatives

- **Install `tau-provider-control` in Astro.** Rejected because Astro does not
  orchestrate sign-in and must not depend on the Django repository.
- **Use Tau's built-in catalog as the product catalog.** Rejected because it
  bypasses Django product policy and recreates the old ownership inversion.
- **Copy Django's catalog into Astro.** Rejected because two release artifacts
  would drift and catalog reads would again imply runtime availability.
- **Put Tau base URLs, headers, and compatibility maps in Django's public
  catalog.** Rejected because they are execution-engine details and some may
  be sensitive.
- **Call the user catalog endpoint on every runtime load.** Rejected because
  the exact session bootstrap already provides the correct authorization and
  consistency boundary.
- **Add a sessionless provider-control endpoint.** Rejected because
  agent-targeted responses already call exact `agent_uid` credential
  hydration; its required execution projection provides the required evidence
  without another route or network round trip.
- **Keep Astro's session-config route as a Django proxy.** Rejected because
  Django's AgentSession model and domain service are canonical; a second public
  mutation surface preserves ambiguous ownership and stale-runtime races.
- **Keep or proxy Astro's provider-control routes during migration.** Rejected
  because all control-plane callers migrate before the Astro cutover and a
  compatibility surface would preserve duplicate ownership, route ambiguity,
  or the process-local attempt store this decision removes.
- **Retain Astro's untyped custom-provider builder registry.** Rejected because
  no production caller uses it and a provider name plus builder does not
  declare enough execution capability to satisfy fail-closed validation.
- **Keep Astro OAuth as a fallback.** Rejected because it recreates
  non-durable sign-in state and two credential writers.
