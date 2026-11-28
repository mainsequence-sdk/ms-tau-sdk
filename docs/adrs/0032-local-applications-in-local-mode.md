# ADR 0032: Local Applications in Local Mode

Status: Accepted — implemented in the SDK for 3.0.7. The owner's decisions were taken on
2026-11-27 (see [Owner decisions](#owner-decisions)).

Date: 2026-11-27

Issue: [#102](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/102)

Amends:

- [ADR 0021: MCP connections on the person's identity](./0021-mcp-connections-on-the-persons-identity.md),
  section 4, whose last sentence, "Local mode has no declared applications", becomes: local mode
  resolves no declared application from the platform, and may name local ones (this ADR); and
  section 6, by naming the credential local mode sends to an application that is not a release.
- [ADR 0022: Time limits and failures of MCP application tool calls](./0022-mcp-application-tool-call-limits-and-failures.md),
  section 3, by adding the failure of a local application's access.

Related:

- [ADR 0005: Authenticated local development mode](./0005-authenticated-local-development-mode.md),
  whose sign-in this ADR reuses.
- [ADR 0011: Independent base-tool and Main Sequence MCP exclusion](./0011-independent-base-tool-and-main-sequence-mcp-exclusion.md).
- [ADR 0023: Skills and resources of declared applications](./0023-skills-and-resources-of-declared-applications.md).
- [ADR 0027: Custom provider credential hydrate endpoints](./0027-custom-provider-credential-hydrate-endpoints.md),
  whose local hydrate address is the precedent for a loopback origin in local mode.
- mainsequence-sdk
  [ADR 0036](https://github.com/mainsequence-sdk/mainsequence-sdk/blob/main/docs/adr/0036-request-scoped-logged-user.md)
  and
  [ADR 0038](https://github.com/mainsequence-sdk/mainsequence-sdk/blob/main/docs/adr/0038-project-mcp-on-fastapi-releases.md),
  which set how a FastAPI application admits a caller in local mode and where it serves its MCP.

## Context

A hosted Agent declares applications in its workflow file. The platform resolves each one to a
deployed release and hands the runtime its name and release in `mcp_applications`, and the runtime
offers four tools for each (ADR 0021, section 4; ADR 0023). Every call obtains the release's RPC
URL and a short-lived token from `resolve-runtime-access`, with the runtime credential and, when
the turn serves a person, the turn's lease proof.

Local mode has none of that. The process runs as the developer's own Main Sequence login (ADR
0005): the CLI's session, a token pair in the environment, or a deprecated pair in the project's
`.env`. Its sessions live in local SQLite, so there is no runtime credential, platform session or
lease proof. `LocalDevelopmentBackend.bootstrap_tau_runtime` answers no `mcp_applications`, and
`RuntimeManager` adds application tools only outside local mode. That was deliberate (#75): a
local Agent has nothing to resolve.

Issue #102 starts a site's Vite front end, FastAPI API, Tau Agent and MetaTables locally. The Agent
is restricted, with `TAU_EXCLUDE_BASE_TOOLS=true` and `TAU_EXCLUDE_MAINSEQUENCE_MCP=true`. The API
serves its MCP at `/mcp`, with 17 tools and six published skills, and an MCP client already reaches
it locally, with discovery and digest-checked skill reads. The local Agent cannot, because nothing
lets it name the API, so it has no tool for the site's work. Restoring the generic tools, copying
the tools into a project extension, a custom adapter and calling the deployed release are all
ruled out.

**How a local application admits a caller.** A FastAPI application that installs
mainsequence-sdk's request identity runs it in local mode when the process has no deployment
markers. It then requires `Authorization: Bearer <token>` on every request and checks the token
with `GET {MAINSEQUENCE_ENDPOINT}/api/v1/users/me/`. A 200 binds that user to the request. Any
other answer is a 401, and a 5xx, a time-out or a network error is a 503 (its ADR 0036). A request
that carries `X-User-UID` must name the same user. The platform refuses a token issued for another
resource, such as the token `resolve-runtime-access` mints for a release, so local admission
answers it with 401 (its ADR 0038, section 3). Today the only token a local application admits is
a Main Sequence user access token for the platform it checks against.

## Decision

### 1. A local application is named in one local-mode setting

`TAU_LOCAL_MCP_APPLICATIONS` is a JSON object. Each key is an application name, and its value is
the loopback origin of the application's API:

```bash
TAU_LOCAL_MCP_APPLICATIONS='{"alpaca": "http://127.0.0.1:8000"}'
```

- **The name** follows the rules for a hosted application: `^[a-z][a-z0-9_]{0,39}$`, and not
  `mainsequence`. A key that appears twice is refused, not overwritten by the later one. The
  developer uses the name the platform gives the deployed application in `mcp_applications`, so
  the Agent's prompt, its skills and the application's skills name the same tools, such as
  `alpaca__call_tool`, locally and hosted.
- **The origin** follows the rule of `TAU_LOCAL_PROVIDER_CREDENTIAL_HYDRATE_URL` (ADR 0027, section
  1): `http` or `https`; host `127.0.0.1`, `::1` or `localhost`; an optional port; no path other
  than `/`; no query, fragment or user information. The MCP endpoint is `{origin}/mcp`, as a
  release's is `{rpc_url}/mcp`, and mainsequence-sdk serves it there (its ADR 0038). `localhost`
  must resolve only to loopback addresses, checked at startup.
- **Local mode only.** The setting without `TAU_LOCAL_MODE=true` fails startup, so a hosted runtime
  can never be pointed at an address.
- **Errors** fail startup. They name the setting and, when there is one, the application, and never
  repeat a value.
- **Unset, empty or `{}`** means no local applications: today's behavior.
- **Order.** The tools follow the order of the object.
- **No secret.** The setting holds only names and origins, so it may live in the project's `.env`
  or in a committed example.

Naming a local application offers its four tools and grants no access, as a declaration does. The
local backend still answers no `mcp_applications`. The runtime builds local applications from the
setting as a kind of their own, which carries an origin and no release UID, so nothing can turn a
local application into a release or a release into a local application.

### 2. The call carries the developer's own access token

Local mode has no runtime credential, platform session, lease proof or delegation, and the platform
cannot mint a token for a developer's local process (ADR 0027). A call to a local application
therefore carries the one credential such an application can verify: the developer's own Main
Sequence access token, from local mode's sign-in (`local_mode_auth`). It is the token local mode
already sends to the platform and to the Main Sequence MCP, from the same source, kept in memory
only.

- **Obtaining it.** Each session a tool opens takes the sign-in's current token. The CLI source
  keeps its token in memory until 60 seconds before it expires, then runs
  `mainsequence auth token --json` again; a token pair is refreshed at the platform's JWT refresh
  path. The CLI keeps the login and renews it. Tau never reads the CLI's credential store.
- **Renewal on 401.** A 401 from the application asks the sign-in once for a new token (the CLI runs
  again, or the pair is refreshed), and the request is sent again. A second 401 ends the call as a
  refusal, `refused` with status 401, as for a release (ADR 0022, section 3). A 403 is not renewed.
  Calls refused at the same time share one renewal, as the CLI source already shares one command
  run.
- **What the application can verify:** who holds the token, as the platform answers `users/me`,
  that is the developer's UID and username. Its tools see the developer through
  `User.get_logged_user()` and decide what each call may do with the developer's ordinary
  permissions. It cannot verify that Tau is calling, or which Agent, session or turn. It sees no
  requester: a local turn serves nobody and carries no delegation (ADR 0021, section 6), so a tool
  that requires a requester refuses, as it does for any local caller today.
- **The same platform.** The application must check tokens against the platform Tau signs in to,
  so `MAINSEQUENCE_ENDPOINT` must be the same in both processes. Otherwise `users/me` refuses the
  token, renewing it does not help, and the call is refused (401).
- **What is never sent:** the runtime credential, a refresh token, a lease token, a caller
  assertion, a delegation, and every `X-MainSequence-*` header. `X-MainSequence-Agent-Session-UID`
  is not sent: there is no platform session, and a local session uid means nothing to the
  platform. `X-User-UID` is not sent either; the application finds the person from the token.
  `Mainsequence-Request-Timeout-Ms` and the caller context (ADR 0031) are sent as to a release.
- **Where it goes:** only to the configured origin. No redirect is followed.
- **Secrecy.** The token never appears in model context, tool arguments, results or their details,
  session history, the UI stream, logs, health, Tau Board or the browser. The browser reaches the
  Agent through its same-origin local Agent proxy and never handles the token.
- **The exposure.** The token is the developer's platform credential, not a token bound to the
  application. Whatever listens on the configured port receives a token that acts as the developer
  on the platform until it expires. Any process the developer runs can already obtain the same
  token from the CLI. What this decision adds is a process of another account on the same machine
  that takes the port while the API is down. On a machine with one account that is nobody. The
  owner accepts this exposure for local development (Owner decisions, 1), and the developer is told
  about it (section 8).

### 3. The four tools are the hosted ones

A local application gets the same four tools as a hosted one, with the same names, descriptions
and parameters, built by the same code. Only the opening of a session differs: section 2 replaces
the release's access, and everything after it is unchanged.

- `<name>__list_tools`, `<name>__call_tool`, `<name>__list_resources` and `<name>__read_resource`
  (ADR 0021, section 4; ADR 0023, section 1).
- **Protocol negotiation:** `server/discover` on `2026-07-28` first, and `initialize` when the
  application does not answer it (ADR 0023, section 2). `skills/list` is asked only when the
  application declares the Skills extension, and every page is read.
- **Confinement and digests:** only what the application lists is read; a file of a listed skill
  must have its listed size and SHA-256 digest; a generated skill's `SKILL.md` is read unchecked;
  binary content is not shown; every read names the application and the skill (ADR 0023, sections
  3 and 4). A published skill is never added to the project's skills, and none of its frontmatter
  is applied.
- **Time limits:** the tool's declared limit or `TAU_MCP_TOOL_TIMEOUT_SECONDS`, and
  `MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS` for each request that opens the session, within
  the turn's limit (ADR 0022, section 2).
- **Failures and retries:** plain words (ADR 0022, section 3, with the row of section 5 below), and
  one more attempt only while the session opens, never after the call is sent (ADR 0022, section
  4).
- **Isolation:** each call opens its own MCP session inside the turn and closes it afterwards. No
  session or catalog is shared between calls, turns or sessions, and nothing is read when a session
  loads.
- **Cancellation:** a cancelled tool call or turn ends the call. A call cancelled before its session
  opens is never sent; a call in flight closes its HTTP response and is never sent again (ADR 0022,
  per-call deadline amendment).

The tools work in every local turn: chat, A2A Message and Task attempt.

The steps only a release has do not run: `resolve-runtime-access`, the wait while a release is
`waking`, the record of a release's warmth, and the question, after a time-out, whether the release
was starting. A local tool call that times out reports `timeout`, never `cold_start_timeout`.

### 4. A local application never becomes a release

- A local application is reached only at its origin. The runtime never asks the platform to
  resolve it, never calls `resolve-runtime-access` for it and never calls a deployed release in its
  place, whatever the failure.
- Local mode still resolves no application from the platform.
- Hosted mode is unchanged: it reads no local setting and takes `mcp_applications` only from the
  startup data.
- `platform_client().call_release()` is unchanged. Project code that names a release UID still
  reaches that deployed release, as local mode allows today; that release is not a local
  application.

### 5. An unavailable application fails the call plainly

Nothing is sent to a local application at startup or when a session loads, and readiness does not
depend on one: the processes of a local stack start together, and an API that answers at startup
proves nothing about the next call. When the application is down, each call fails and says so.

ADR 0022's table gains one row:

| What happened | What the model reads |
| --- | --- |
| The local sign-in could not provide a token (no CLI session, the CLI did not answer, the refresh was refused) | Your Main Sequence sign-in could not provide access to the local `<name>` application. |

Its details are `failure: access_unavailable` and `phase: access`. Every other failure keeps its
words: nothing listening on the port reads "The `<name>` application could not be reached."
(`unreachable`), after one more attempt to open the session.

The details of every local application result, and its `runtime.mcp_application.failed` event,
carry `target: "local"`. Neither carries the origin, a token or the CLI's output. The sign-in's own
event, `local.auth.cli_token.completed`, keeps naming its outcome, such as `no_session`, so the
developer knows to run `mainsequence login`.

### 6. The effective catalog

The tools of a local application are SDK tools, like a hosted application's, and neither exclusion
removes them: `TAU_EXCLUDE_MAINSEQUENCE_MCP` removes only the platform's own MCP connection (ADR
0021 amends ADR 0011 so). With `TAU_EXCLUDE_BASE_TOOLS=true`, `TAU_EXCLUDE_MAINSEQUENCE_MCP=true`
and no project extension, a local session's tools are exactly:

- `read`, limited to skill files (ADR 0011);
- the four tools of each local application; and
- `task_request_input` and `task_request_authorization`.

There is no `write`, `edit`, `bash`, unrestricted `read` or Main Sequence MCP tool. A project
extension's tools are added as today, under the same complete-catalog check, and the catalog digest
in health covers the application tools.

### 7. Health

In local mode `/health` reports `local_mcp_applications`: the names, in order, and never the
origins. It is `null` in managed mode.

### 8. The developer is told where the token goes

- **At startup.** When local applications are configured, the runtime logs one warning,
  `local.mcp_applications.token_sent`, on every sink. Its message names each application and its
  origin, and says that the developer's Main Sequence access token is sent there and that any
  program listening on that port receives it. For example:

  ```text
  Local mode sends your Main Sequence access token to the local applications in
  TAU_LOCAL_MCP_APPLICATIONS: alpaca at http://127.0.0.1:8000. Any program listening on that
  port receives it and can act as you on Main Sequence until it expires. Run only your own API
  there.
  ```

  The message shows the origins because the developer needs them to check the ports, and a valid
  origin holds no secret: the setting refuses user information. It never shows the token. Nothing
  is logged when no local application is configured.
- **In the packaged skill.** The `tau_local_development` skill tells a coding agent that sets
  `TAU_LOCAL_MCP_APPLICATIONS` for the user to say the same to the user when it sets it: which
  port receives their token; that it is their own Main Sequence access token, valid on the
  platform until it expires, not a token limited to the application; that only their own API
  should listen there, bound to `127.0.0.1`; and that this should not be used on a machine that
  other accounts share. The skill's sentence "The user JWT is sent only to Main Sequence" becomes:
  only to Main Sequence and to the local applications named in `TAU_LOCAL_MCP_APPLICATIONS`.
- **In the Security and access guide,** under local mode, with the same facts.

## Owner decisions

Taken by the owner on 2026-11-27.

1. **The credential: accepted for local development.** The call sends the developer's own access
   token (section 2), the only token local admission verifies today. On a machine with one account
   the exposure adds almost nothing, because any program the developer runs can already obtain
   the same token from the CLI. The developer is told where the token goes, at startup and by the
   packaged skill (section 8). A token bound to one local application is not pursued now (see
   Rejected alternatives).
2. **The setting: one JSON object,** `TAU_LOCAL_MCP_APPLICATIONS`, from name to origin (section 1).
3. **The workflow declaration: not checked.** Local mode does not read the Agent's workflow file,
   and how it runs one declared Agent is still open (ADR 0026, "Not decided here"). So the setting
   stands alone and can name an application the deployed Agent does not declare. The developer
   controls both files, and a name that differs from the hosted one shows at once as different
   tool names. The check is added when local mode learns to run one declared Agent.

## Requirements of the platform and other repositories

None: the platform, tdag-django and mainsequence-sdk are unchanged, and a local application needs
nothing beyond mainsequence-sdk's request identity. A token bound to one local application would
need tdag-django and mainsequence-sdk; it is not filed now, and would be filed as issues in those
repositories, not changed from here, if shared development machines come to need it.

## Rejected alternatives

- **The deployed release in place of the local API,** or its token sent to the local API. #102
  rules out a deployed fallback: the Agent would act on deployed code and data while the developer
  tests local ones. Local admission refuses a release token anyway.
- **No credential, as for the local hydrate address of ADR 0027.** Local admission requires a
  Bearer, and the application must know its caller to authorize each tool.
- **A token bound to the local application, minted by the platform.** It would remove the exposure
  of section 2, because the application could not replay it against the platform. But tdag-django
  would have to mint it and mainsequence-sdk's local admission accept it, and #102 would wait for
  both. On a machine with one account it protects against almost nothing (Owner decisions, 1).
- **Requiring each local name to be declared in the Agent's workflow file.** It needs local mode to
  read that declaration first, which ADR 0026 left open (Owner decisions, 3).
- **A JSON list shaped like `mcp_applications`, or one variable per application.** The object is
  the shortest form that maps a name to an origin (Owner decisions, 2).
- **A static token in configuration.** A secret in a file, to store, rotate and leak. Main Sequence
  issues none.
- **A token from the browser or the model.** #102 forbids a bearer token in browser code or model
  arguments, and either would reach model context, history or logs.
- **A full MCP URL instead of an origin.** Releases serve MCP at `/mcp` (mainsequence-sdk ADR 0038),
  and an origin reuses ADR 0027's check.
- **Any other private address,** such as a LAN address or a container name in a compose network.
  The developer's token would leave the machine or cross a network.
- **Probing the application at startup, and failing startup when it does not answer.** A local
  stack starts its processes together, so a slow API would stop the Agent, and an answer at startup
  proves nothing about later calls.
- **One long-lived connection per process, as the Main Sequence MCP has.** Catalogs and skill lists
  would be kept across turns, and a changed skill file would be checked against a stale list.
- **A project extension or a custom adapter in the consuming repository.** #102 rules both out:
  each consumer would copy tools and credential handling the SDK already owns.

## Consequences

- A local Agent reaches its local API with the tools it has when hosted, under the same names, so
  its prompt and skills need no local variant.
- The local application sees the developer, not an Agent. A tool that requires a requester refuses
  locally.
- One tool call is several requests: discovery or the handshake, the tool or skill list, then the
  call. mainsequence-sdk's local admission asks `users/me` for each, which took 2 to 4 seconds per
  request in its own measurement, unless the application sets
  `install_request_identity(app, local_caller_cache_seconds=60)`. Without it, a discovery request
  that waits longer than MCP's ten seconds falls back to `initialize`, and the skills are not
  listed. The guide for consuming repositories recommends the setting.
- The developer's token is exposed to whatever listens on the configured port (section 2). Every
  start with local applications configured logs a warning about it (section 8).
- A local stack that runs the Agent and the API in separate containers cannot use this. Both run on
  the developer's machine, or share its network.

## Implementation and acceptance

Implemented for 3.0.7, in one change.

1. **Settings.** `TAU_LOCAL_MCP_APPLICATIONS` is parsed with duplicate keys refused; names are
   checked as hosted names are; origins with ADR 0027's loopback check, under this setting's name;
   `localhost` must resolve only to loopback addresses; and the setting fails startup outside local
   mode.
2. **The connector.** A local application opens `MCPConnectionClient` at `{origin}/mcp` in `auto`
   mode, with the timeouts of a release, and authenticates with local mode's sign-in, renewed once
   on 401. The release-only steps (`resolve-runtime-access`, the readiness wait, warmth and the
   start question) move to the release's connector, so the four tools no longer name a release.
3. **The session.** In local mode the manager adds the four tools of each local application, in
   order; hosted mode is unchanged.
4. **Health** reports `local_mcp_applications`.
5. **The startup warning** `local.mcp_applications.token_sent` of section 8.
6. **Documentation, in the same change:** the [runtime contract](../reference/runtime-contract.md)
   (declared applications and the local-mode route boundary), the
   [settings reference](../reference/settings.md), the
   [Security and access guide](../reference/security-model.md) (local mode: the token and its
   exposure), guidance for consuming repositories (naming the API, the same `MAINSEQUENCE_ENDPOINT`,
   `local_caller_cache_seconds`, both exclusions), the packaged `tau_local_development` skill
   (section 8) and project customization skill, the changelog, and the amended sentences of ADR
   0021 and ADR 0022.

### Test plan

Tests use a real MCP 2.x server (`MCPServer`, Streamable HTTP) on `127.0.0.1` and an ephemeral
port, behind an admission that mirrors mainsequence-sdk's local mode: the Bearer is checked against
a stub `users/me`, and anything else answers 401.

1. **Startup.**
   - A valid setting gives four tools for each application, in order.
   - An invalid name, `mainsequence`, a name longer than 40 characters and a repeated key each fail
     startup, naming the setting and the application, never the value.
   - A non-loopback host (`10.0.0.5`, `0.0.0.0`, `example.com`, `127.0.0.1.nip.io`), a path, a
     query, user information, port 0 and a scheme other than `http` or `https` each fail startup.
     So does `localhost` that resolves to a non-loopback address.
   - The setting without `TAU_LOCAL_MODE=true` fails startup.
   - Unset, empty and `{}` give today's catalog.
   - Startup, readiness and session load send nothing to the origin, and readiness reports ready
     while nothing listens on it.
   - With local applications configured, startup logs `local.mcp_applications.token_sent` once,
     naming each application and its origin and never the token. Without them it logs nothing.
2. **A real loopback MCP call.**
   - `alpaca__list_tools` lists the server's tools, and `alpaca__call_tool` calls a read-only tool
     and returns its result.
   - `alpaca__list_resources` lists the published skills after `2026-07-28` discovery.
     `alpaca__read_resource` returns a skill file whose digest matches, and refuses one whose bytes
     changed (`unverified`).
   - The server receives `Authorization: Bearer <the developer's token>` and no `X-MainSequence-*`
     or `X-User-UID` header.
   - Results, details, logs, health and history never contain the token.
   - An ordinary local chat turn on the mock provider, whose model lists the skills, reads one and
     calls a tool, ends normally, and its history shows the calls without the token.
3. **Never a release.**
   - While the local application is down, answers 5xx or answers 401, the platform stub receives
     no `resolve-runtime-access`, and no request reaches a host other than the origin.
   - A tool call that times out reports `timeout`, and the platform is not asked whether a release
     was starting.
4. **Unavailable target.**
   - With nothing listening, the call reports `unreachable` after one more attempt to open the
     session.
   - A 502 while opening is retried once; a 502 on the tool call is not, and the server sees one
     call.
   - A sign-in that cannot answer (the CLI exits 1, or does not answer in time) reports
     `access_unavailable` with `phase: access`, and the CLI's output appears in no result.
5. **Cancellation.**
   - A call cancelled before its session opens is never sent, and the server sees nothing.
   - A call cancelled while its token is being obtained returns promptly.
   - A call cancelled while the tool runs closes its HTTP response; the server sees the connection
     end, the call is not sent again, and other calls keep running.
   - A call that outlives its turn ends with the turn.
6. **Expired authentication.**
   - The server answers 401 to the first token: the sign-in is asked once for a new one, the request
     is sent again with it, and the call succeeds.
   - A second 401 reports `refused` with status 401, after exactly one renewal and two requests.
   - A 403 reports `refused` with no renewal.
   - A CLI token within 60 seconds of expiry is renewed before the session opens.
   - Calls refused at the same time share one CLI run.
7. **The effective catalog.**
   - With `TAU_EXCLUDE_BASE_TOOLS=true`, `TAU_EXCLUDE_MAINSEQUENCE_MCP=true`, one local application
     and no project extension, the session's tools are exactly `read`, the four application tools,
     `task_request_input` and `task_request_authorization`.
   - The `read` refuses a path outside the skills, and no Main Sequence MCP connection is opened.
   - The health catalog digest is the same across sessions.
   - With a project extension, the complete-catalog check still holds. With neither exclusion, the
     application tools sit beside the coding tools and the Main Sequence MCP tools.

Manual acceptance follows #102: the consuming site's local Agent, restricted by both exclusions,
discovers and reads the local API's published skills and calls an authorized read-only tool.

### What the tests verify

Built for 3.0.7. The tests are `tests/unit/test_local_mcp_applications.py` (startup),
`tests/contract/test_local_application_mcp.py` (a real MCP 2.x server served by uvicorn on
`127.0.0.1`, behind a Bearer admission) and the local-mode catalog tests in
`tests/unit/test_runtime_manager.py`. They fail on the source before this change, and each fails
when its behavior is taken out: the registration, the renewal on 401, the loopback check, and
skipping the start question for a local application.

- **Point 1, startup:** verified in full. That nothing is sent at startup or session load is shown
  by no MCP session being opened, with readiness reported.
- **Point 2, a real call:** verified, except the token's absence from health and session history
  (it is absent from every result, its details and the logs), and except the chat turn on the mock
  provider.
- **Point 3, never a release:** verified while the application is down, refuses with 401 or 403,
  answers 502 or times out: the platform stub is never asked. That no other host is contacted is
  not checked.
- **Point 4, an unavailable target:** verified, with a stand-in sign-in that raises in place of a
  CLI that exits 1 or does not answer.
- **Point 5, cancellation:** verified. The call cancelled while the tool runs ends and leaves the
  application usable; that the server sees its connection end is not checked.
- **Point 6, expired authentication:** verified with a stand-in sign-in and with local mode's own
  token pair (`JWTAuth`). The CLI's renewal before expiry and its one command run for concurrent
  forced renewals are covered by `tests/unit/test_cli_token_auth.py`, not repeated here.
- **Point 7, the effective catalog:** verified with both exclusions on and both off, including a
  startup answer that names a release, which adds nothing. The skill-limited `read` and the
  complete-catalog check with a project extension are covered by existing tests. The catalog
  digest across sessions is not checked.
- **Not done:** the manual acceptance against the consuming site's API.
