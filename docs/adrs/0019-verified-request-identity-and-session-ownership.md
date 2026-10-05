# ADR 0019: Verified Request Identity and Session Ownership

Status: Accepted — implemented

Date: 2026-10-05

Issues: [#59](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/59) and
[#60](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/60)

Amends:

- [ADR 0002: Runtime and protocol contracts](./0002-runtime-and-protocol-contracts.md) by
  authenticating every inbound request of a hosted runtime and authorizing access to sessions; and
- [ADR 0005: Authenticated local development mode](./0005-authenticated-local-development-mode.md)
  by refusing local mode in a process that carries the platform's hosting settings.

## Context

When the platform hosts an Agent, its launcher serves the application only after the application
declares installed request identity in `app.state.mainsequence_request_identity`. `create_app()`
declared none, so an Agent built on the SDK did not start on such a launcher.

The runtime also took its caller from gateway headers it cannot verify. A user caller was whatever
`X-User-UID` said. The two routes for the platform's own background calls,
`/internal/a2a/task-dispatch` and `/internal/a2a/task-caller-delivery`, checked no credential. A
request that named a session UID could act on that session, whoever had created it. The platform
returns the creator of every session as `created_by_user_uid`, and the SDK never read it.

The platform signs a short-lived assertion for each request it forwards to a hosted runtime: a
caller assertion when a person or a workload calls, and a platform assertion for its own background
calls. The SDK does not depend on the `mainsequence` package and does not import it, so it verifies
the assertions itself.

## Decision

### 1. Hosted mode is the launcher's rule

The runtime is hosted when `MAINSEQUENCE_CALLER_AUTH_MODE=assertion`, or when any of `APP_NAME`,
`FASTAPI_PUBLIC_BASE_URL`, `MAINSEQUENCE_CALLER_ASSERTION_ISSUER` or
`MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL` is set to a non-empty value. That is the launcher's own
rule, and `TauSDKSettings.request_identity_mode` applies it: `assertion` when hosted, `local`
otherwise. Any other value of `MAINSEQUENCE_CALLER_AUTH_MODE` is a settings error, and
`MAINSEQUENCE_CALLER_AUTH_MODE=local` does not turn hosting off.

The platform sets these variables only when it hosts the runtime. Local mode (`TAU_LOCAL_MODE`)
refuses to start when the rule says hosted, because a local process has no gateway that could
forward an assertion.

### 2. `create_app()` declares request identity in both modes

`create_app()` sets `app.state.mainsequence_request_identity` to
`{"installed": True, "mode": <mode>, "public_ingress": ()}`. `public_ingress` is always an empty
tuple: an Agent has no route that the platform admits without a caller. The launcher reads the
declaration from the application object that it serves, so a project serves the application that
`create_app()` returns and does not mount it inside another application.

In hosted mode `create_app()` also installs `RequestIdentityMiddleware` as its innermost middleware,
so request logging and CORS still wrap what it rejects. It builds the verifier from the trust
settings and fails with a `ConfigurationError` that names each missing or invalid one:
`MAINSEQUENCE_CALLER_ASSERTION_ISSUER`, `MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL` (an HTTPS URL),
`APP_NAME` (the release UID) and `MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID`, both canonical
lowercase UUIDs.

### 3. The assertion contract

Both assertions arrive in the `X-MainSequence-Caller-Assertion` header as a JWS signed with
`EdDSA` (Ed25519). The protected header is exactly `alg`, `kid` and `typ`. `kid` is the RFC 7638
thumbprint of the signing key.

| | Caller assertion | Platform assertion |
| --- | --- | --- |
| `typ` | `mainsequence-caller-assertion+jwt` | `mainsequence-platform-assertion+jwt` |
| Claims, exactly | `iss`, `aud`, `sub`, `resource_release_uid`, `organization_environment_uid`, `team_uids`, `is_organization_admin`, `iat`, `nbf`, `exp` | `iss`, `aud`, `resource_release_uid`, `organization_environment_uid`, `iat`, `nbf`, `exp` |

In both:

- `iss` equals `MAINSEQUENCE_CALLER_ASSERTION_ISSUER`;
- `aud` is the single string `urn:mainsequence:fapi:<APP_NAME>`;
- `resource_release_uid` equals `APP_NAME`;
- `organization_environment_uid` equals `MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID`;
- `iat`, `nbf` and `exp` are integers with `nbf <= iat < exp <= iat + 300`, and the current time
  lies between `nbf` and `exp`. There is no clock leeway.

In a caller assertion, `sub` is a canonical lowercase User UID: a person, or the runtime principal
of a calling workload. `team_uids` is a sorted list of unique canonical UIDs, and
`is_organization_admin` is a boolean.

The verifier fetches the key set from `MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL` when it first needs
it, without following redirects, with the backend connect, read, write and pool timeouts and the
backend response size limit. It uses only Ed25519 signing keys whose `kid` is their thumbprint. The
set stays fresh for the `max-age` of the response; a response without one is kept until a token
names a key the set does not hold. A token that names an unknown `kid` refreshes the set once. A
key set that cannot be fetched, is not JSON, holds no usable key, or is too large is unavailable,
and the verifier never uses a set that is no longer fresh in its place. A failed refresh keeps the
fresh keys it already holds.

### 4. Routes require one assertion kind

In hosted mode:

- `/internal/*` accepts only a platform assertion, and every other route only a caller assertion.
  The route is the path Starlette's router matches.
- A missing, invalid, expired, duplicated or wrong-kind assertion gets 401 with
  `{"detail": "A valid caller assertion is required."}` or the platform variant. An unavailable
  key set gets 503 with `{"detail": "Caller authentication is unavailable."}`. Both carry
  `Cache-Control: no-store`.
- There is no fallback to `X-User-UID` or any other header.
- `OPTIONS` passes without an assertion.
- A WebSocket handshake follows the same rule and is closed with code 1008, or 1013 when the key
  set is unavailable. The SDK serves no WebSocket route.

The launcher answers `/ms-health-deployment` and `/__mainsequence/healthz` before the application,
so the platform's probes need no assertion. `/health`, `/ready` and `/version` are application
routes and need a caller assertion like any other.

### 5. Handlers receive the verified caller

For an admitted request the middleware sets `request.state.user` to the verified caller (`uid`,
`team_uids`, `is_organization_admin`), `request.state.user_uid`, `request.state.auth_outcome`
(`authenticated`), `request.state.resource_release_uid` and
`request.state.organization_environment_uid`. A platform assertion has no caller, so `user` and
`user_uid` are `None`. Rejected requests carry `auth_outcome` `rejected` or `unavailable`, and
`OPTIONS` carries `not_applicable`. The request log states the verified user, or none, instead of
the `X-User-UID` header.

Turn provenance takes a `user` caller from the verified `sub` and never reads `X-User-UID`.
Agent-caller provenance still comes from the gateway's `X-Caller-*` headers, and `X-Caller-Kind`
stays required on the chat and A2A message routes.

### 6. Only the owner or an Organization admin addresses a session

In hosted mode, a request that addresses an existing session must come from the session's owner or
from an Organization admin. The owner is the session's `created_by_user_uid`, compared with the
verified `sub`. An Organization admin is a caller whose assertion says `is_organization_admin`; the
runtime does not read the session for an admin. Anyone else gets 403 with
`{"detail": "Only the session's owner or an Organization admin can address this session."}`
before the runtime acts. A JSON-RPC call gets the same HTTP 403.

The check covers:

- `POST /api/chat`, `GET /api/chat/session-model` and `POST /api/chat/session/cancel`;
- `POST /api/a2a/v1/message:send` and `message:stream`, for the session in `message.contextId`,
  before any file part is stored, and for the session of a continued or existing Task;
- `GET /api/a2a/v1/tasks/{task_id}`, `POST .../{task_id}:cancel` and `GET .../{task_id}:subscribe`,
  for the Task's session;
- `GET /api/a2a/v1/tasks?contextId=...` and `GET /api/a2a/v1/extendedAgentCard`; and
- the same operations through `POST /api/a2a/rpc`.

A Task list without `contextId` keeps only the Tasks of sessions the caller may address.
`PUT /api/chat/session-model`, `GET /api/chat/model-providers` and the `/api/local/v1/*` routes
answer 409 outside local mode before they touch a session.

The runtime never creates a session in managed mode. Chat and A2A require an existing session, and
the platform records `created_by_user_uid` when it creates one for the User who asked. The
platform's own calls to `/internal/*` carry a platform assertion with no caller. They address the
sessions the platform names and create none, so the ownership check does not apply to them.

### 7. Nothing changes outside hosted mode

Outside hosted mode no middleware is installed and every route behaves as before. Local mode keeps
its own owner scope (ADR 0017 and ADR 0018). A managed runtime that is not hosted still reads its
caller from the gateway headers.

### 8. Dependencies

The SDK depends directly on PyJWT with the `crypto` extra and on `cryptography`. Both were already
installed through `mcp`. It still neither depends on nor imports `mainsequence`.

## Consequences

- A hosted runtime answers only requests that carry the platform's assertion. Anything that called
  a hosted runtime directly, including `/health` or `/ready` probes, must go through the platform
  or use the launcher's own endpoints.
- A request that addresses a session costs one read of that session from the platform, unless the
  caller is an Organization admin. A Task list without `contextId` reads each listed session once.
- An application on an earlier SDK declares no request identity and does not start on a launcher
  that requires it.
- An assertion issued while the runtime's clock is behind the platform's can be refused as not yet
  valid. The platform issues `iat` and `nbf` as the current second, so the margin is the delivery
  time.
- Every token with an unknown `kid` costs one fetch of the key set. There is no limit on how often
  that happens.

## Verification

`tests/unit/test_caller_assertions.py` covers the verifier: both assertion kinds; another `typ`,
algorithm or header; an extra or missing claim; expiry, a lifetime over 300 seconds, and
non-integer or unordered times; another issuer, audience, release or Environment; an invalid
caller; a signature by another key; one refresh for an unknown `kid`; caching for `max-age`; an
unavailable, invalid or oversized key set; and the configuration errors.

`tests/unit/test_request_identity.py` covers the middleware and the declaration in hosted, managed
and local mode; internal against other routes and the wrong kind on each; `OPTIONS`; WebSocket;
the request log; the verified caller in handlers and in turn provenance; and session ownership for
the owner, an Organization admin and another user on every route in section 6, including JSON-RPC
and the filtered Task list.
