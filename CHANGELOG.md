# Changelog

## Unreleased

- **Breaking: one delegation envelope on every call made for the work
  ([#78](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/78), ADR 0021 amendment).** This
  release works only with the matching platform change, deployed in the same cutover; there is no
  deprecation period, and earlier releases, including 2.0.6, stop acting for people once that
  platform is deployed.
  - Every hosted Main Sequence MCP call names the turn's session, and carries the person's
    delegation (`mainsequence.ai/delegation/v1`) while the turn serves a person. The platform's
    per-tool labels (`mainsequence.ai/requires-requester/v1`,
    `mainsequence.ai/requires-caller-session-proof/v1`) are no longer read, and a turn that serves
    nobody keeps its MCP tools: its calls are the Agent's own.
  - Declared application tools run in every turn, with a token for the person the turn serves or
    the Agent's own token when it serves nobody. A refused delegated call is reported and never
    retried as the Agent.
  - The person a turn serves comes only from the platform's answer to the turn start, whoever
    called; dispatch, delivery and Task answers are no longer read for it. Delegated work serves
    the person the platform records for it.
  - `platform_client()` replaces `requester_client()`, with no alias. By default a call carries the
    delegation while the turn serves a person; `delegation="none"` never carries it and
    `delegation="required"` refuses the call before sending when the turn serves nobody. Outside a
    turn no call carries a delegation, and in local mode calls use the signed-in person's own
    credential.
  - Delegated calls use the person's ordinary permissions, including administrative ones; the
    statement people are shown changes accordingly.
  - The SDK no longer hides the withdrawn private Secret entry operations, and the local health
    field `mcp_session_proof_limited_tool_count` is removed.

- A new packaged skill, `tau_security_and_access`, teaches building project tools that act for the
  person a turn serves: which delegation each tool should use, a worked example of tools that use
  a person's own Secret without exposing it to the model, and how sharing decides what a delegated
  call can reach. `ms-tau skills sync` now installs five skills.

- Documentation now distinguishes requester-bound reads and supported writes from workload grants,
  states the write risk and platform rollout requirements, and includes conversation/Task
  permissions and denial recovery. The Security and access guide keeps its `security-model.md`
  path. Provider selection settings and troubleshooting, the public readiness-hook reference, and
  the exported API compatibility list are complete. This changes documentation only.

## 2.0.6 — 2026-10-07

- The documentation no longer describes the platform's internal implementation. It no longer names
  the backend framework, its repositories, ADRs, classes or commits, and calls the backend "the
  platform". The archived Astro documents (`docs/history/astro`) and the ADR 56 migration workspace
  (`docs/migration/adr-56`) are removed. Provider-control validation errors now begin with
  "Platform provider-control" instead of naming the backend framework.
- A new [agent security model](./docs/reference/security-model.md) page explains who an Agent acts
  as, who can address its sessions, how its grants are capped, why only an Organization admin can
  let it act for the person it serves, how Secrets are handled, what reaches the model, what code in
  its process can reach, and what local mode changes. The README links to it.
- Main Sequence MCP is the first connection of a general MCP connection primitive
  ([ADR 0021](./docs/adrs/0021-mcp-connections-on-the-persons-identity.md), step 1). One client
  and one adapter serve a remote MCP server over Streamable HTTP, each connection with its own tool
  prefix, tool filter, session and catalog. The platform connection offers the same tools, names,
  schemas and results as before.
- A Main Sequence MCP tool that the platform marks with `mainsequence.ai/requires-requester/v1`
  runs for the turn's requester (ADR 0021, step 2). The SDK sends the turn's private session proof
  with it, and refuses it before sending in a hosted turn that serves nobody. Local mode runs it as
  the signed-in person. Tools without the mark are unchanged.
- An application an Agent declares in its workflow file has its MCP endpoint registered (ADR 0021,
  step 3). The platform returns each declared application in the startup data as an
  `mcp_applications` entry with `name` and `resource_release_uid`, and the runtime offers
  `<name>__list_tools` and `<name>__call_tool` for it. Both call the application's `/mcp` endpoint
  for the turn's requester, with a session opened for the call and a token issued for that person,
  and are refused in a turn that serves nobody. No UID or URL is configured in the project.

## 2.0.5 — 2026-10-07

- Tau never offers `secret.list`, `secret_entry.start`, `secret_entry.status` or
  `secret_entry.cancel` to the model. A platform that has not yet deployed the removal of private
  Secret entry still lists them, and 2.0.4 offered them like any other Main Sequence MCP tool. They
  are now left out of the session's tool list, as `agent_session.resolve_runtime_access` is.

## 2.0.4 — 2026-10-07

- Private Secret entry is removed, and ADR 0020 with it. Tau no longer gives the Main Sequence MCP
  tools `secret_entry.start`, `secret_entry.status` and `secret_entry.cancel` special handling,
  no longer saves `mainsequence.secret_entry` session entries, and no longer checks pending entry
  requests before each turn. That check stopped the turn whenever it could not verify a request,
  so a request that could never be checked again blocked every later turn of its conversation. A
  person adds a Secret in Command Center or with the `mainsequence` CLI, and a workload receives
  access through the access it declares or is granted; hosted Agents still never read or write
  Secret values. Entries that 2.0.3 saved stay in session history, outside the model context, and
  are ignored. Until the platform stops listing the entry tools, Tau offers them like any other
  Main Sequence MCP tool.

## 2.0.3 — 2026-10-07

- A turn that a caller delivery resumes serves the person who asked for the delegated work. When
  `POST /internal/a2a/task-caller-delivery` resumes a hosted session, the turn presents no
  assertion. It names the delivery in `caller_delivery_uid` on the
  `PATCH /api/v1/agent-sessions/<uid>/tau-runtime-activity/` transition that marks it active, and
  the platform answers with the requester it recorded for the turn: the person who asked for the
  turn that delegated with `resume_caller`. `current_requester()` returns that person, with empty
  `team_uids`, and `requester_client()` reads for them, only when the delivery signal names the
  same User with `requester_identity_type` `human`. Local mode and a runtime that is not hosted
  name no delivery.
- Main Sequence MCP no longer offers `agent_session.resolve_runtime_access` to the model. Its
  result carries a short-lived runtime token for direct-runtime clients, and Tau's generic MCP
  projection copied that token into model-visible tool content and into tool details, which reach
  stream events and session history. Tau never needs the token: agent-to-agent turns use
  `a2a.send_message`. The operation is left out of the session's tool list, so
  `mainsequence__agent_session_resolve_runtime_access` is no longer a tool; every other MCP tool
  and its result are unchanged. Direct-runtime clients that call the operation themselves are
  unaffected. See the 2026-10-06 amendment of ADR 0002
  ([issue #65](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/65)).
- Extension tools can read with the access of the person a turn serves, its requester, when an
  Organization admin enabled the Agent for it. When a hosted runtime starts a chat or A2A Message
  turn, it sends the verified caller assertion of the request in `X-MainSequence-Caller-Assertion`
  with the `PATCH /api/v1/agent-sessions/<uid>/tau-runtime-activity/` transition that marks the
  turn active. The platform answers with `requester_user_uid`, the person it recorded as the turn's
  requester, or null. A hosted request that creates a Task (`POST /api/v1/agent-tasks/`) or
  continues one (`POST /api/v1/agent-tasks/<uid>/continue/`) sends the same header with that call,
  so that the platform records the Task's requester. A Task attempt that the request runs itself
  serves the request's verified caller when the platform's Task answer names that User as
  `requester_user_uid` with `requester_identity_type` `human`; a Task attempt started by the
  platform's dispatch takes its requester from the dispatch's same two facts. Two new public
  functions:
  - `current_requester()` returns that person inside the turn (`uid`, `team_uids`), or `None` for
    Agent callers, the platform's other calls, local mode, a runtime that is not hosted, and code
    outside a turn;
  - `requester_client()` returns a client bound to the turn. `request(method, path, ...)` calls a
    platform API path with the runtime's credential and `X-MainSequence-Acting-For-Session`,
    `X-MainSequence-Lease-Holder` and `X-MainSequence-Lease-Token`, sent only to the platform base
    URL. `call_release(release_uid, method, path, ...)` obtains access through
    `resolve-runtime-access` and calls the application with only its bearer token, kept for the
    turn while it is valid. A 403 whose `code` is `requester_binding_invalid` or starts with
    `runtime_lease_` raises a `PermissionError` with that code, and without a requester
    `requester_client()` raises one at once.

  The assertion stays in its request's scope and goes only to the turn start, Task creation or Task
  continuation of that request. It is not sent once expired, never by the turn start of a Task
  attempt, never on the platform's own calls, and never in local mode. The assertion, the lease
  proof, the runtime credential and application tokens never reach a log line, a persisted entry,
  model context, a tool result, the UI stream or history, and the binding ends with the turn. The
  `tau-project-customization` skill teaches tools to use both functions, to return only business
  results, and states what people are told about this access. Upgrade note: nothing changes for
  an Agent that does not use them. Requester-bound calls need a platform that records turn and Task
  requesters; until it does, `current_requester()` returns `None`. See the 2026-10-06 amendment of
  ADR 0019 ([issue #66](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/66)).
- `MainSequenceClient.list_model_providers(organization_environment_uid=...)` reads the
  model-provider catalog of that Organization Environment. It raised `TypeError` instead, so
  local-mode chat could not list the providers of a configured Environment through
  `GET /api/chat/model-providers`. The Environment is now sent as the `organization_environment_uid`
  query parameter, as the other list calls send theirs; the call without an Environment is
  unchanged ([issue #68](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/68)).
- A hosted runtime admits the Agent that delegated to a child session. When Agent A delegates to
  Agent B through `a2a.send_message`, the platform creates B's child session for the person who
  owns A's session, and A addresses it with its own credential, so the caller assertion names A's
  workload User. B's runtime refused every such request with 403. It now also admits that caller
  when the session's `parent_session_agent_uid` names an Agent and the platform's directory
  (`GET /api/v1/users/<uid>/`, read with the runtime credential) shows the caller as a workload
  User whose `agent_uid` is that Agent: Message send and stream, JSON-RPC, and Task continuation,
  reads, list and cancel. The `X-Caller-*` headers never admit a caller. A session without a
  parent, another Agent's workload, and a person who is not the owner still get 403, and a lookup
  that fails refuses the request. One request looks a User up at most once, and nothing is kept
  after it. The admitted Agent is never a requester: `current_requester()` stays `None` in its
  turns and in the Task attempts its requests run. See the 2026-10-06 amendment of ADR 0019
  ([issue #67](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/67)).

## 2.0.2 — 2026-10-06

- Runtime credentials require the projected workload identity token file. The runtime credential
  exchange proves the credential only with the token in the file that
  `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE` names, read again for every exchange, and every
  exchange request carries exactly `credential_id` and `workload_identity_token`. The bootstrap
  secret mode is removed: the SDK no longer reads `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET` and never
  sends `credential_secret`, and `TauSDKSettings` no longer has a `runtime_credential_secret` field.
  Managed startup without `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE` fails with
  `Missing runtime credential settings: MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE`, and a missing,
  unreadable, or empty token file fails the exchange with an error that names the file. There is no
  fallback. Retries after HTTP 429 and 503, the immediate failure on HTTP 401, and the rule that
  the token never leaves the exchange are unchanged; the `proof` field of the
  `runtime.auth.exchange.*` log events is always `workload_identity_token`. Tau Board no longer
  reports whether `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET` is set. Upgrade note: a runtime
  configured with `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET` and no token file no longer starts; set
  `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE` to the path of the projected token. See the 2026-10-06
  amendment of ADR 0002.

## 2.0.1 — 2026-10-05

Published as 2.0.1: the number 2.0.0 belongs to a release that was yanked on 2026-09-19, and PyPI never reuses a version.

- `create_app()` installs request identity, so a hosted Agent starts on a platform launcher that
  requires it. The application declares it in `app.state.mainsequence_request_identity`:
  `{"installed": True, "mode": "assertion", "public_ingress": ()}` when Main Sequence hosts the
  runtime and `"mode": "local"` otherwise. Hosted means `MAINSEQUENCE_CALLER_AUTH_MODE=assertion`
  or any of `APP_NAME`, `FASTAPI_PUBLIC_BASE_URL`, `MAINSEQUENCE_CALLER_ASSERTION_ISSUER` or
  `MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL` set, the launcher's own rule. A hosted runtime admits a
  request only with the platform's signed assertion in `X-MainSequence-Caller-Assertion`: a
  platform assertion on `/internal/*`, a caller assertion on every other route, `/health`,
  `/ready` and `/version` included. It verifies the Ed25519 signature against the key set at
  `MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL`, refreshed once for an unknown `kid`, plus the exact
  type, the exact claim set, the issuer, the release in `APP_NAME`, the Environment in
  `MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID`, and a lifetime of at most 300 seconds. A missing,
  invalid, duplicated or wrong-kind assertion gets 401, and a key set that cannot be fetched gets
  503. The runtime no longer takes a caller from `X-User-UID` in hosted mode: a user turn is
  stamped with the verified `sub`, while Agent-caller provenance still comes from the gateway's
  `X-Caller-*` headers. Handlers read the verified caller from `request.state.user`. A hosted
  configuration that lacks a setting fails at `create_app()` and names it, and local mode refuses
  to start with the hosting settings. Outside hosting nothing changes. The SDK now depends directly
  on PyJWT (`crypto`) and `cryptography`, and still not on `mainsequence`. Upgrade note: callers of
  a hosted runtime must go through the platform, which forwards the assertion, and platform probes
  use the launcher's own endpoints. See ADR 0019
  ([#59](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/59)).
- A hosted runtime lets only a session's owner or an Organization admin address that session. The
  owner is the User the platform recorded as the session's `created_by_user_uid`, compared with the
  verified caller; an Organization admin is a caller whose assertion says `is_organization_admin`.
  Anyone else gets 403 before the runtime acts, on chat, the session model, session cancellation,
  A2A Message send and stream, Task get, cancel, subscribe and list, and the extended Agent Card,
  over REST and JSON-RPC alike. A Task list without `contextId` returns only Tasks of sessions the
  caller may address. The platform's own `/internal/*` calls are not subject to the check, and
  local mode keeps its own owner scope. See ADR 0019
  ([#60](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/60)).

## 1.4.0 — 2026-10-05

- A managed runtime can prove its runtime credential with a projected workload identity token
  instead of the bootstrap secret. With the new `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE` setting,
  the runtime credential exchange reads that file for every exchange, because the token in it is
  rotated, and sends `credential_id` with `workload_identity_token`. In this mode the SDK never
  reads or sends `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`, and the configuration check no longer
  requires it. A missing, unreadable, or empty token file is a configuration error that names the
  file, and the exchange never falls back to the secret. The token stays inside the exchange: it is
  not kept in the settings, the environment, or a file, and never appears in a log or an error
  message. Without the setting, the exchange sends the secret as before. In both modes, an
  exchange answered with HTTP 429 or 503 is sent again up to three times, after waiting as long as
  `Retry-After` asks (at most 60 seconds) or 1, 2, then 4 seconds without it; a longer
  `Retry-After` fails at once. HTTP 401 fails at once, without a retry or another proof. The
  `runtime.auth.exchange.completed` log event names the proof in `proof` and now also reports
  failed exchanges. See the 2026-10-05 amendment of ADR 0002
  ([#56](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/56)).
- Printing or logging the settings no longer shows the runtime credential secret. `repr()` and
  `str()` of `TauSDKSettings` showed `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET` in plain text, and so
  did every log event that carried the settings object. Three other objects showed a credential
  the same way, and now leave it out of `repr()` and `str()`:
  - `AccessToken`: the Main Sequence access token;
  - `ProviderCredential`: the key that an `organization_custom` credential carries in `headers`;
  - `TauRuntimeBootstrap`: the hydrated provider credentials in `provider_credentials`.

  A settings validation error no longer repeats the configured values. When the secret or a
  local token was the last value configured, the error printed its last characters. The error
  still names the variable and the problem. The SDK reads and sends every value as before, and
  `runtime_credential_secret` is still a `str`
  ([#57](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/57)).

## 1.3.0 — 2026-10-05

- Project skills stay available with `TAU_EXCLUDE_BASE_TOOLS=true`. Tau lists skills in the system
  prompt only when a tool named `read` exists, so excluding the four coding tools also removed
  every skill in `.tau/skills/`. The SDK now registers a `read` tool in their place that opens only
  the files of the skills Tau discovered for the session: a skill's `SKILL.md` and the files in its
  directory, such as `references/*.md`. It rejects every other path, including other workspace
  files, system paths, `..` escapes, and symlinks that resolve outside the skill, so the process
  environment and its credentials stay out of reach. It keeps Tau's `offset`, `limit`, and output
  limits. With both exclusions set, the catalog is project tools, this `read`, and the two Task
  controls. In this mode a project extension that registers `read` fails on session load and on
  reload. With base tools included nothing changes. See the 2026-10-05 amendment of ADR 0011
  ([#54](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/54)).

## 1.2.14 — 2026-10-01

- The repository structure reference now shows Tau Board: the tree lists `packages/tau-board/` and
  `tests/e2e/`, and the wheel is described as containing both the `ms_tau_sdk` and `ms_tau_board`
  import packages, Tau Board's static assets, and the `ms-tau` and `tau-board` commands.
  Documentation only; the package is unchanged from 1.2.13.

## 1.2.13 — 2026-09-30

- Local mode no longer needs a token in the environment or in the project `.env`. With neither
  `MAINSEQUENCE_ACCESS_TOKEN` nor `MAINSEQUENCE_REFRESH_TOKEN` set, it asks a Main Sequence CLI
  for the access token with `mainsequence auth token --json`: log in once with
  `mainsequence login`. The SDK looks for the CLI in the new `MAINSEQUENCE_CLI` setting, then
  beside the Python interpreter, then on `PATH`. It keeps the token in memory, reuses it until 60
  seconds before it expires, and asks again after a rejected request. It refuses an answer for
  another backend. A missing CLI, a CLI too old for the command, a missing or expired session, a
  machine without a credential store, a time-out, and malformed output each have their own error.
  The SDK still does not depend on or import the `mainsequence` package and does not read the
  CLI's credential store. The token pair stays supported in the process environment, for
  launchers and CI, and with both tokens set the CLI is never run. A pair read from the project
  `.env` file still works but is deprecated: startup logs one warning that names the file and the
  two variables, and `mainsequence refresh-token` run in that directory removes them. `/health`
  and `/ready` report the source in `mainsequence_auth_source` (`cli`, `environment`, or
  `env_file`; `null` in managed mode), and Tau Board shows it in Connect. The owner scope of local
  chat sessions and direct A2A conversations is derived from the subject of the CLI token, so a
  user keeps the same local sessions with either source. See the 2026-09-30 amendments of ADR 0005
  and ADR 0017.
- Tau Board again reads the local state the SDK writes. 1.2.12 raised the local SQLite schema
  version to 6 without raising the version Tau Board accepts, so the board refused every store
  that 1.2.12 had created or opened: the State, session, Task, and Task-log views returned
  "Unknown Tau local SQLite schema version". Schema 6 only adds the `local_chat_sessions` table,
  and the board's queries are unchanged. The board still accepts exactly one schema version, so a
  store last opened by 1.2.11 or earlier is refused until Tau starts once and migrates it. A Board
  test now reads a store written by the SDK's local backend, so a schema change the board has not
  followed fails the Board suite.

## 1.2.12 — 2026-09-27

- Local `/api/chat` sessions now survive a UI reload and a Tau restart. Local mode records each chat
  session and adds `GET /api/local/v1/chat-sessions` to list them and
  `GET /api/local/v1/chat-sessions/{sessionUid}/history` to read one back. The history uses the
  shape of the platform's `GET /api/v1/agent-sessions/{uid}/history/`: user and assistant text,
  reasoning, and tool calls with their arguments, results, and `isError`, projected from the Tau
  transcript by the platform's rules. A turn still running is returned in `inProgressMessage`.
  `GET /api/local/v1/agent` returns the Agent's name and description from the workspace's
  `.agents/agent_card.json`. A local chat turn now runs to its durable end when its client
  disconnects or reloads, and only `POST /api/chat/session/cancel` stops it. A second turn for a
  session that is still running returns 409 `session_busy`. After a Stop, the next turn in a local
  session used to fail with "Runtime cancellation was requested" until the idle runtime was
  evicted; the session's next turn now runs on a fresh runtime. Managed chat behavior is unchanged.
  With `MAINSEQUENCE_TAU_TRUSTED_ORIGINS` set, CORS also exposes `X-Agent-Session-Uid` and
  `x-vercel-ai-ui-message-stream`. See ADR 0018. Fixes #47.
- Added `MAINSEQUENCE_TAU_PROVIDER_TIMEOUT_SECONDS`, the HTTP timeout for model-provider calls.
  It was fixed at 60 seconds for every provider hydrated from the platform, with no setting
  or environment variable to change it. A self-hosted OpenAI-compatible provider such as an
  Ollama gateway sends no bytes until its first token, so a cold model load plus a long
  prompt exhausted all three attempts, and Tau reported only an empty transport failure. The
  default stays 60 seconds, and an explicit `timeout_seconds` passed to `ProviderFactory.build`
  still takes precedence.

## 1.2.11 — 2026-09-27

- Added authenticated-process-scoped discovery and bounded hydration for direct local A2A Message
  conversations. The SDK now persists exact public requester/responder Messages independently from
  Tau execution entries, returns stable context and Message identities across restart, supports
  cursor pagination and exact completed-request replay, and exposes
  `GET /api/local/v1/conversations` plus its bounded Message-history route. Existing internal
  sessions are not heuristically reconstructed. Fixes #45.
- Added a version-locked `tau-ai==0.4.2` compatibility patch for OpenAI-compatible transport
  failures that exhausted retries with an empty terminal message. The SDK now preserves the
  concrete HTTPX error type, transport phase, retry evidence, total provider duration, one failure
  UID, and privacy-filtered traceback locations without changing the imported Tau package. Tau
  Board groups the propagated lifecycle records and renders a human-readable failure incident
  before the raw event JSON. The patch is tracked upstream in
  [huggingface/tau discussion 745](https://github.com/huggingface/tau/discussions/745) and must be
  removed, not retargeted, when Tau publishes a conforming fix.

## 1.2.10 — 2026-09-26

- Restored the managed A2A Task creation and continuation adapter removed before the 1.2.9
  release. It sends the platform's required snake_case `message_id` and `reference_task_ids` fields
  and omits empty `extensions`, which the backend defaults. Local Task messages retain the
  complete A2A binding shape. A nonempty extension URI list on a managed Task returns HTTP 400
  until the backend can persist it. A clean-wheel check now guards this contract. Fixes #42.

## 1.2.9 — 2026-09-26

- Added bounded A2A Task Message history with hard-cut A2A v1 role projection, canonical durable
  status Messages, exact idempotent settlement replay, and reference-only status events.
- Correlated every Task attempt with one immutable Tau turn and exact entry boundaries, including
  local Task-owned lease fencing, committed/abandoned resolution, and late-write rejection.
- Rebuilt Tau Board Task detail around Overview, Conversation, Result, Execution, and Technical
  evidence, with byte-exact streamed Artifact reconstruction and bounded secret-redacted execution
  entries.
- Guaranteed A2A failure observability: standard terminal failed status events, conformant safe
  status Messages, explicit terminalization-unknown transport errors, and bounded request waiting.
- Added SDK-owned local Task reconciliation. Unclaimed submitted Tasks resume after restart;
  exhausted starts fail terminally, while stale working attempts with uncertain side effects fail
  as ambiguous instead of being blindly executed twice. Health, structured logs, and Tau Board now
  expose recovery and failure evidence.
- Added Tau Board's Agent view for readable effective Agent Card inspection, source-grouped tool
  catalog inspection, project extension diagnostics, and bounded registered-entry source viewing.
- Added an explicit local project-tool workbench with schema-generated inputs, authoritative
  validation, short-lived single-use confirmation, exact loaded-tool execution, streamed bounded
  results, timeout and cancellation. It does not call the model or write conversation/Task history;
  project code still has its normal real side effects.
- Added creation and completion/status timing to the A2A Task view. Completion uses the standard
  A2A `task.status.timestamp`; creation is joined from Tau's existing local SQLite Task state, so
  the wire contract remains standard.

## 1.2.8 — 2026-09-24

- Added independent `TAU_EXCLUDE_BASE_TOOLS` and `TAU_EXCLUDE_MAINSEQUENCE_MCP` process settings.
  Deployments can expose only project extension tools plus the two required A2A Task controls.
  The runtime skips MCP connection and resource guidance when excluded, rejects project overrides
  of the Task controls, and checks the effective catalog on load and after extension reload.
- Documented the settings for managed workflow `env_vars` and local runtimes in ADR 0011, the
  reference guides, and the version-matched `tau-project-customization` skill.

## 1.2.7 — 2026-09-21

- Bundle the local Tau Board command, Python package, and UI assets in the `ms-tau-sdk` wheel.
  `ms-tau-sdk[tau-board]` now selects Board's tested dependency bounds without requiring a
  second PyPI project. The Board remains a separate local process. SDK releases no longer wait
  for an unpublished `ms-tau-board` distribution.
- A merge to `main` is the release. The release workflow runs on the merge instead of on a
  hand-pushed tag: it publishes the version `pyproject.toml` declares, creates the tag `vX.Y.Z` and
  the GitHub release after the upload, and brings the release merge and the next patch number to
  `development` in one push, so no `X.Y.Z.devN` can follow the final `X.Y.Z`. A merge that still
  declares a released version, or whose tag already names another commit, publishes nothing.
- Stopped discarding the only diagnostic a message-less provider failure carries. When Tau ends a
  turn with `stopReason: "error"` and no `errorMessage`, the status code from the `provider_error`
  diagnostic is composed into the message the user sees (`Provider error (HTTP 402)`) instead of
  the bare `Provider error` constant, `agent.model.failed` now emits `status_code`, and the
  assistant-ui `error` frame carries `status` and `error_code`. An unpaid account and a
  misconfigured credential are distinguishable again, in the stream and in the logs. The
  message/body split is unchanged: the provider's message is still forwarded verbatim and its raw
  response body still never leaves the process.

## 1.2.6 — 2026-09-21

- Made `pyproject.toml` the only source of the version. `development` declares the release being
  worked toward, development releases are that number with `.devN`, and the final release is the
  same number as a tag on `main`. The repository used to say `1.2.5` while it published
  `1.2.6.devN`, and tagging `v1.2.6` then failed against `pyproject.toml`. PyPI is now read only as
  a guard that refuses a development build of an already released version, and the release
  workflow raises the patch number on `development` after it publishes.
- Stopped sending `execution_context` in local-mode provider hydration. The backend never declared
  the field on `POST /api/v1/model-provider-credentials/hydrate/` and dropped it silently;
  authentication already distinguishes local development from runtime execution, so the request
  carries no execution-context discriminator.
- Moved durable Tau runtime state out of the installed package. Built-in extension state, provider
  credentials, project trust, and agent-call diagnostics now live under
  `MAINSEQUENCE_TAU_STATE_ROOT/<workspace-hash>`, defaulting to an XDG-style user state directory,
  instead of `ms_tau_sdk/resources/`. An installed wheel no longer writes to its own site-packages
  directory, so a read-only install works, and a test run no longer leaves runtime files in `src`
  for the next build to package. Managed-mode and local-mode behavior is otherwise unchanged.
- Made the release gate reject runtime residue. `scripts/verify_distribution.py` now holds an
  explicit allowlist of the entries and file types the package ships, and refuses any `state/`
  directory, so this class of leak fails the gate instead of shipping.
- Adopted one branch and release standard. A final release is a `vX.Y.Z` tag on `main`, and the
  publish workflow refuses a tag whose commit `main` does not contain. Every push to `development`
  publishes one `X.Y.Z.devN` release automatically, after the same quality gate as `quality.yml`
  and only when it passes. `pip` and `uv` ignore development releases unless one is pinned exactly.
- Made `pyproject.toml` the only file that declares the version. The consumer fixture resolves the
  SDK from the checkout instead of naming a version, and the contract tests read the declared
  version rather than repeating it.

## 1.2.5 — 2026-09-19

- Persist local-mode operational events as workspace-scoped, rotated, private JSON Lines while
  retaining console sinks and leaving managed-mode logging unchanged.
- Preserve exception types and traceback locations without persisting raw exception messages.

## 1.2.4 — 2026-09-19

- Retired Agent-targeted one-shot responses, the deployment execution snapshot setting, and
  Agent-UID-only provider hydration. Chat, local and durable A2A, and session snapshots remain.
- Aligned managed AgentTask execution with the platform's canonical Task actions for dispatch claim,
  attempt start and settlement, and output create, append, and finalize. Removed the nonexistent
  attempt-Message and caller-delivery backend routes; signed caller delivery now persists its
  idempotent platform event before the runtime acknowledges the platform's push.

## 1.2.3 — 2026-09-18

- Hard-cut the managed runtime contract to the canonical Tau 0.4.2 handshake: lease acquire and
  renew requests no longer send the retired `lease_purpose` field, and `tau_runtime_version`
  identifies the installed `tau-ai` distribution instead of the SDK package version.
- Added the Tau 0.4.2 `custom_message` discriminator to typed durable-entry responses.

## 1.2.2 — 2026-09-18

- Replaced the retired AgentSession `checkpoint-lease/*` client routes with the canonical
  harness-neutral `runtime-lease/*` acquire, renew, and release routes.
- Aligned internal A2A task-dispatch and caller-delivery signals with the platform's current contract.

## 1.2.1 — 2026-09-17

- Added durable local-mode A2A Message and Task execution across REST, JSON-RPC, and SSE, backed by
  the workspace SQLite store without creating platform Agent or AgentSession records.
- Narrowed `local_mode_capability_unsupported` to agent-targeted responses and internal/platform
  routing features that genuinely require registered identity or callback delivery.
- Expanded the packaged local-development skill and public documentation with the exact local A2A
  persistence, identity, restart, polling, and unsupported-routing boundaries.

## 1.2.0 — 2026-09-17

- Unified managed and local backend selection on the established `MAINSEQUENCE_ENDPOINT`
  environment variable.
- Added explicit `ms-tau skills sync`, `list`, and `path` commands with an atomically managed
  `.agents/skills/ms_tau_sdk/` namespace and version provenance.
- Moved TAU repository integration, local development, project customization, and A2A host-adapter
  guidance into skills packaged with the SDK.
- Defined the hard ownership boundary that leaves platform ontology and canonical A2A semantics in
  the platform while keeping SDK-versioned implementation mechanics in this distribution.

## 1.1.1 — 2026-09-17

- Fixed local-mode provider hydration to preserve the canonical credential envelope consumed by
  the shared Tau runtime parser, so a hydrated credential reaches provider execution.
- Added focused parser coverage and an end-to-end local chat regression through the real Tau
  session runtime.

## 1.1.0 — 2026-09-17

- Added authenticated local development mode: workspace-scoped SQLite Tau state, lazy local chat
  sessions, explicit provider/model selection, dependency-free user-JWT refresh, real Main
  Sequence provider hydration and MCP, and typed rejection of registered-agent orchestration.
- Added a release guard that forbids a `mainsequence` distribution dependency or package import;
  the local authentication boundary is environment variables plus the public HTTP refresh API.

## 1.0.0 — 2026-09-16

- Established Main Sequence TAU SDK as the `ms-tau-sdk` Python distribution, `ms_tau_sdk`
  namespace, and `ms-tau` process command.
- Made every process project-workspace-bound and exposed `create_app` plus `TauSDKSettings` as the
  public construction surface.
- Preserved the existing Main Sequence transports, runtime-credential client, provider hydration,
  durable and sessionless Tau execution, persistence, streaming, A2A, MCP, and lifecycle behavior.
- Adopted Tau-native project `.tau` configuration and project-owned extensions with no parallel SDK
  prompt configuration or deployment enable flag.
- Removed container, Compose, Kubernetes, image, overlay, wheelhouse, and optional web/search/video
  tool ownership from this project.
- Added allowlisted wheel/sdist verification, isolated installed-process checks, release checksums,
  dependency/provenance metadata, build attestation, and PyPI trusted-publishing automation.
- Declared the reviewed Python API, command, settings, project-configuration, and tested wire
  contracts as the first stable compatibility surface.
- Removed residual provider-specific image-build and push material from the historical archive.
