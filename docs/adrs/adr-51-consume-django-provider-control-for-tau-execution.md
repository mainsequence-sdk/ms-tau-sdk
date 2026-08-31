# ADR 51: Consume Django Provider Control for Tau Execution

Status: Accepted; implementation pending

Date: 2026-08-31

Related decision: `tdag-django/docs/agents/adr/adr-024-django-owned-model-provider-control-plane.md`

Supersedes: `docs/reference/adr-interactive-provider-signin.md`

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

Tau's built-in provider catalog remains available inside Astro only as an
execution-capability registry. It can supply transport details that belong to
the engine, including API adapters, base URLs, compatibility flags, reasoning
parameter mappings, and provider-specific headers.

It must not be returned as the Main Sequence catalog or used to decide what
the product publishes. A provider/model appearing in Tau means only that this
Astro release knows how to execute it. A provider/model appearing in Django
means the platform may select it. Execution requires both statements to be
true.

Astro will not add a local JSON/TOML copy of Django's catalog, synchronize a
catalog at process startup, or read `~/.tau/catalog.toml` in managed runtime
mode.

### Bootstrap contract

Execution continues through the existing Tau bootstrap route. Django will add
one `provider_control` object to that response:

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

The object is additive to the existing bootstrap response. It does not rename
the route, bump the Tau bootstrap version, or change the public catalog schema.
The coordinated Django implementation must update its serializer, schema
documentation, and focused tests before Astro requires the field.

During rollout, Astro accepts a missing `provider_control` object only behind
the existing runtime-contract compatibility mode. After Django is deployed,
managed `tau_runtime_contract = "v1"` execution requires the object. No new
permissive fallback is added.

### Fail-closed execution validation

Before constructing a Tau provider, Astro validates all of the following:

1. the provider-control schema is supported and its digest is well formed;
2. the projected provider and model exactly match the session selection;
3. the credential envelope is for that exact provider;
4. the selected provider/model exists in Tau's execution registry, unless an
   explicitly registered Astro runtime adapter owns it;
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

When a credential is near expiry, Astro rehydrates through Django. Django owns
refresh and returns the current canonical envelope. Astro must not refresh a
token with Tau OAuth code and flush it back as the target path. The existing
flush operation remains only for compatibility with older runtimes until its
separate retirement is approved.

Astro does not blindly replay an inference request after an authentication
failure if the provider may already have accepted or streamed it. It obtains a
fresh credential for the next safe operation and surfaces a redacted error for
the ambiguous one.

### Legacy Astro control-plane routes

The following Astro routes are legacy compatibility surfaces, not canonical
APIs:

- `GET /api/chat/get_available_models`;
- `GET /api/models/catalog`;
- `GET /api/model-providers`;
- provider sign-in status/manual/cancel routes; and
- provider signoff.

No new caller may use them. They must not keep a copied catalog or process-
local attempt store. Where an identity-preserving proxy to Django is possible,
they may temporarily return Django's projection. Otherwise they remain frozen
until telemetry confirms that the already migrated Command Center and MCP
callers no longer use them. Removing routes is a separate public-contract
approval and release change.

After that gate, Astro deletes `providers/catalog.py`,
`providers/signin.py`, their dependency wiring, and the OAuth imports that are
not used by execution. Runtime credential hydration and `ProviderFactory`
remain.

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

## Minimal Implementation

The Astro change is intentionally small:

1. Add one typed `ProviderControlExecution` bootstrap model.
2. Add one validator at the boundary between bootstrap parsing and
   `ProviderFactory`.
3. Make selection, thinking, and media validation intersect Django's
   projection with Tau execution metadata.
4. Preserve the existing in-memory provider construction and exact credential
   rehydration path.
5. Add focused unit and contract tests for valid execution, mismatched
   provider/model/API, unsupported schema/transport, missing credentials,
   thinking/media narrowing, and redaction.
6. Remove local catalog and sign-in ownership only after the compatibility
   gate.

The first execution slice maps directly onto existing code:

| File | Change |
| --- | --- |
| `src/astro/backend/models.py` | Type `provider_control` and add it to `TauRuntimeBootstrap`. |
| `src/astro/providers/factory.py` | Validate the Django projection against Tau execution metadata before building the provider. |
| `src/astro/runtime/manager.py` | Pass bootstrap evidence into the factory and record only safe digest/version diagnostics. |
| `tests/unit/test_runtime_manager_adr49.py` | Cover bootstrap acceptance and fail-closed rejection. |
| `tests/unit/test_provider_credentials.py` | Cover execution compatibility, credential matching, and redaction. |

No new backend client method or network round trip is needed: the evidence and
credential arrive in the existing bootstrap response.

This decision does not add a provider-control client package, a new Astro
service, a database model, a worker, an OAuth state machine, a filesystem
store, a local catalog artifact, a second provider factory, or a generic
plugin framework.

## Rollout

1. Django adds and documents the additive bootstrap projection while accepting
   current Astro requests.
2. Astro parses and validates the projection, with compatibility mode for the
   deployment overlap.
3. Deploy Django before making the field mandatory in managed Astro v1
   runtimes.
4. Observe bootstrap validation failures by safe code, provider, model, Astro
   version, and catalog digest; never log credential material.
5. Freeze, then retire legacy Astro control-plane routes after caller telemetry
   and explicit route-removal approval.
6. Retire runtime credential flush only after every supported runtime uses
   Django refresh/rehydration.

Rollback keeps the additive Django field and rolls Astro back to the preceding
consumer. It does not restore Astro as catalog or sign-in authority.

## Consequences

Model discovery and sign-in remain available when no Astro runtime exists.
Django owns one durable product contract, while Astro keeps the Tau-specific
knowledge required to execute it. A newly published Django model cannot run on
an older Astro release unless that Tau engine explicitly supports it, and the
failure occurs before agent execution with safe diagnostics.

The cost is one small additive bootstrap projection and coordinated rollout.
This is preferable to installing Django's provider-control package in Astro,
duplicating the catalog, or moving Tau execution internals into Django's
public catalog.

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
- **Keep Astro OAuth as a fallback.** Rejected because it recreates
  non-durable sign-in state and two credential writers.
