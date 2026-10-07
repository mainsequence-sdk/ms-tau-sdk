# ADR 0019: Verified Request Identity and Session Ownership

Amended 2026-10-07 for [issue #74](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/74):
align section 9 and the user-facing statement with the accepted requester read/write policy in
[ADR 0021](./0021-mcp-connections-on-the-persons-identity.md). This documentation correction
changes no runtime behavior; platform opt-in and rollout requirements still determine availability.

Status: Accepted — implemented

Date: 2026-10-05

Issues: [#59](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/59) and
[#60](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/60)

Amended 2026-10-06: a hosted chat or A2A Message turn presents the verified caller assertion of
the request that started it when it marks the turn active, and a hosted request that creates or
continues a Task presents it with that call, so that the platform can record who asked. Extension
tools read the verified requester of a turn or Task attempt with `current_requester()` and read
with that person's access through `requester_client()` (section 9,
[issue #66](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/66)).

Amended 2026-10-06: a turn that a caller delivery resumes names the delivery when it marks the
turn active, and serves the requester the platform records for it: the person who asked for the
turn that delegated the work (section 9).

Amended 2026-10-06: the Agent that delegated to a child session may address it. When Agent A
delegates to Agent B, the platform creates B's child session for the person who owns A's session,
and A addresses it with its own credential, so the caller assertion names A's workload User. The
runtime admits that caller when the session names A as its parent session's Agent and the
platform's directory shows the caller to be A's workload User. Such a caller is never a requester
(section 6, [issue #67](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/67)).

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

### 6. Only the owner, an Organization admin, or the delegating Agent addresses a session

In hosted mode, a request that addresses an existing session must come from the session's owner,
from an Organization admin, or, for a delegated child session, from the workload User of the Agent
that delegated to it. The owner is the session's `created_by_user_uid`, compared with the verified
`sub`. An Organization admin is a caller whose assertion says `is_organization_admin`; the runtime
does not read the session for an admin. Anyone else gets 403 with
`{"detail": "Only the session's owner or an Organization admin can address this session."}`
before the runtime acts. A JSON-RPC call gets the same HTTP 403.

Amended 2026-10-06. When Agent A delegates to Agent B, the platform creates B's child session for
the person who owns A's session, and A sends the request with its own credential, so the verified
`sub` is A's workload User, neither the owner nor an admin. The runtime admits that caller only
when all of these hold:

- the session names its parent session's Agent in `parent_session_agent_uid`, which the runtime
  reads on its own session with its runtime credential;
- the platform's directory, `GET /api/v1/users/<sub>/` read with the runtime credential, shows the
  `sub` as a workload User (`identity_type` `workload`); and
- that workload's `agent_uid`, the Agent its release serves, equals `parent_session_agent_uid`.

The `X-Caller-*` headers never take part: they describe a caller and never admit one. A session
without a parent, the workload of another Agent, and a person who is not the owner still get 403.
A lookup that fails refuses the request. One request looks a User up at most once; nothing is kept
after the request. The admitted Agent is never a requester (section 9): its assertion names a
workload, not a person, so its turns and the Tasks it creates have none, whatever the platform
answers.

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

### 9. The turn's requester and requester-bound calls

Amended 2026-10-06.

An Agent that an Organization admin enabled for it may use supported platform reads and writes,
and call other platform applications, with the access of the person whose request a turn is serving:
the requester. The platform keeps that authority. The runtime proves only which of its own sessions it is working on,
and the platform finds the person in its own records. No call the runtime makes names a person.

**Recording who asked.** When a hosted runtime marks a chat or A2A Message turn active, with
`PATCH /api/v1/agent-sessions/<uid>/tau-runtime-activity/`, `runtime_activity` `working` and the
new `active_turn_uid`, it sends the verified caller assertion of the request that started the turn
in `X-MainSequence-Caller-Assertion`, beside its own credential. The platform verifies it and
answers with the usual runtime state plus `requester_user_uid`: the person it recorded as the
turn's requester, or null. It records nobody for an Agent caller, for a person who does not own
the session, or when no assertion is sent.

- The middleware keeps the verified raw assertion in the request's own scope, not in
  `request.state` and not in a context variable. Only the chat route and the A2A Message routes
  (`message:send` and JSON-RPC `SendMessage` answered with a Message, including a strict JSON
  repair turn of the same request) read it, and they hand it to the turn they start.
- The turn presents it only with the transition that marks it active, and only while the assertion
  is valid; an expired assertion is not sent. A turn start repeated after a stale lease presents it
  again.
- A Task attempt never presents one when it starts its turn: the Task's creation or continuation
  already did. Local mode and a runtime that is not hosted never present one.
- It is never logged, persisted, or placed in model context, a tool result, the UI stream or
  history, and it is dropped with its request.

**Recording who asked for a Task.** When a hosted request creates a Task
(`POST /api/v1/agent-tasks/`, from `message:send` answered with a Task, `message:stream` and
JSON-RPC `SendStreamingMessage`) or continues one (`POST /api/v1/agent-tasks/<uid>/continue/`),
the runtime sends the request's verified caller assertion in the same header beside its own
credential, under the same rules: only while it is valid, never in local mode or outside hosting,
never on the platform's own calls, and never in the Task's body. The platform records the person
as the Task's requester and names them in the answer's `requester_user_uid` and
`requester_identity_type`, and its later dispatches of the Task name the same facts.

**A turn that resumes a caller delivery.** When the platform's
`POST /internal/a2a/task-caller-delivery` resumes a hosted session for delegated work, the turn it
starts has no request of its own and presents no assertion. The transition that marks it active
names the delivery in `caller_delivery_uid` instead. The platform answers with `requester_user_uid`:
the person who asked for the turn that delegated the work with `resume_caller`, while they still
own the session, or null. Local mode and a runtime that is not hosted name no delivery.

**The turn's requester.** `current_requester()` returns, inside a turn, an immutable object with
`uid` and `team_uids`, or None:

- a chat or A2A Message turn: the verified caller, only when the platform answered with a
  non-null `requester_user_uid` equal to that caller's UID. `team_uids` come from the assertion.
  The Organization-admin flag is never part of it, because requester-bound access is member-level;
- a Task attempt that the request creating or continuing the Task runs itself (`message:send`
  answered with a Task, `message:stream`, a continuation): the request's verified caller, only when
  the platform's answer to that creation or continuation names the same User as
  `requester_user_uid` with `requester_identity_type` `human`;
- a Task attempt started by the platform's dispatch (`POST /internal/a2a/task-dispatch`): the
  dispatch's `requester_user_uid` when its `requester_identity_type` is `human`, with no Team
  UIDs. Only a hosted runtime reads these facts, because only it verifies the platform assertion
  on its internal routes;
- a turn that resumes a caller delivery: the delivery's `requester_user_uid` when its
  `requester_identity_type` is `human`, only when the platform's answer to the turn start names
  the same User, with no Team UIDs;
- otherwise None: Agent callers, the platform's other calls, local mode, a
  runtime that is not hosted, and code outside a turn. A caller that section 6 admitted as the
  Agent that delegated to the session is a workload User, so it is never the requester, whatever
  the platform answers.

The turn binds its requester for its own duration and ends the binding when it ends. Detached SDK
work never inherits it, and a task a tool leaves running sees no requester after the turn.

**Requester-bound calls.** `requester_client()` returns a client bound to the current turn and
raises a `PermissionError` with `code` `requester_binding_invalid` when the turn has no
requester.

- `request(method, path, ...)` calls a platform API path relative to the platform base URL with
  the runtime's credential and `X-MainSequence-Acting-For-Session` (the turn's session),
  `X-MainSequence-Lease-Holder` and `X-MainSequence-Lease-Token` (the runtime's current lease on
  it). They are the values of the runtime's private caller-session proof.
- `call_release(release_uid, method, path, ...)` obtains access with
  `POST /api/v1/resource-releases/<release_uid>/resolve-runtime-access/`, sent with the same
  headers. The answer's `access` is `{"mode": "token", "token": <bearer>, "rpc_url": <base URL>}`.
  The client calls `rpc_url` plus `path` with only `Authorization: Bearer <token>`, keeps the token
  for the turn while it is valid, replaces it shortly before it expires, and replaces it once after
  a 401. The application answers as the requester.
- The binding headers and the runtime credential go only to the platform base URL. A tool passes a
  path, never a URL, and cannot set `Authorization` or any `X-MainSequence-*` header. Redirects are
  not followed. The answer is returned on a request copy that carries no header.
- A 403 whose JSON `code` is `requester_binding_invalid` or starts with `runtime_lease_` raises a
  `PermissionError` carrying that code: the turn is over, the requester's access was removed, more
  than 24 hours passed since the request, or the Agent is not enabled to act for its requester.
  Every other answer is returned to the tool as it is.
- The platform enforces member-level rights for requester-bound reads and opted-in writes.
  Supported writes may create, change, run, share, or delete only with the person's permission.
  Secret values, credential/token management, billing, and admin operations remain excluded;
  SDK-managed application-access resolution is the limited credential exception.

**What people are told.** Every SDK document and skill that describes this access uses this
statement:

> **This Agent works with your identity.** It can read, create, change, run,
> share or delete only what your permissions allow through supported operations,
> only while serving your request, and for at most 24 hours after you ask. It
> never receives your admin powers or Secret values, and access is checked on
> every call. Your Organization's administrator approved it to work this way.

The statement describes requester-bound operations, not the Agent's separate workload grants or
local human credentials. Prompt injection can steer the Agent into unintended reads or writes
within the person's rights. Changes, sharing, or deletion can persist after the binding ends;
only administrators enable this authority. See the
[Security and access guide](../reference/security-model.md#availability) for rollout status.

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
- A caller that is neither the owner nor an admin of a delegated child session costs one directory
  lookup per request.
- A turn start, a Task creation and a Task continuation carry one more header. A platform that
  does not record requesters answers without `requester_user_uid`, and the turn or Task attempt
  then has no requester.
- While it serves a requester, an enabled Agent's code can read what that person can read.

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
and the filtered Task list. With the platform's real delegation combination, a person-owned child
session whose parent is Agent A's and a caller assertion naming A's workload User, it covers A's
admission to Message send and stream, JSON-RPC, and Task continuation, read, list and cancel; the
refusal of another Agent's workload, a person who is not the owner, a session without a parent and
a failed lookup; and one lookup per request.

`tests/unit/test_requester_identity.py` covers section 9: the assertion on the turn-start
transition of hosted chat, A2A Message, JSON-RPC and strict JSON repair turns, for person and
Agent callers; none on Task attempts, after expiry, in local mode or outside hosting; the requester
the platform recorded, nobody, or someone else; the assertion on Task creation and continuation
and the requester of an attempt the request runs, for each answer the platform can give; the
dispatched Task requester and its rejected variants; the binding ending with the turn; the three headers only to the platform origin; the
release access flow, its cache, its renewal near expiry and after a 401; the refusal codes; and a
hosted turn end to end, with a project tool on a real Tau session, whose logs, persisted entries,
stream, tool result and model context contain no assertion, lease token, runtime credential or
application token. `tests/contract/test_session_persistence_client.py` freezes the header and the
`requester_user_uid` answer of the transition, and `tests/contract/test_backend_client.py` the
header and the requester answer of Task creation and continuation.
