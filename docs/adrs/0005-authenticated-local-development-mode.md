# ADR 0005: Authenticated Local Development Mode

Status: Accepted — SDK implementation complete; live backend capability evidence remains external

Date: 2026-09-16

Amended: 2026-09-17 — local A2A Message and Task execution is required; an A2A protocol Task does
not require a platform AgentSession.

Amended: 2026-09-19 — local mode durably writes structured, privacy-filtered operational logs to a
workspace-scoped file without changing managed-mode logging.

Amends, when accepted:

- [ADR 0002: Runtime and protocol contracts](./0002-runtime-and-protocol-contracts.md); and
- [ADR 0004: Minimal bundled tool boundary](./0004-minimal-bundled-tool-boundary.md).

## Context

Main Sequence TAU SDK currently has only the managed execution path. A durable Tau session can
start only after a Main Sequence `Agent` and `AgentSession` have been created in the platform. The
runtime then bootstraps that session from the backend and writes its history, activity, lease,
cancellation, snapshot, and task state back to the platform.

That contract is correct for deployed agents but creates unnecessary friction during repository
development. A developer changing `.tau` instructions, extensions, hooks, or project code must
first create platform objects. Every experimental conversation then appears as real platform
session state. Repeated development runs pollute the user's AgentSession history and make local
debugging depend on control-plane state that is unrelated to the code being tested.

The absence of a local session mode is therefore an SDK gap. Now that the SDK is installed directly
inside a Main Sequence project, the project must be able to start the same Tau runtime in its
workspace without first registering an Agent or AgentSession.

Local development is not disconnected or offline execution. The agent still needs Main Sequence
for two operational capabilities:

1. The developer selects an exact provider and model for the local process. Main Sequence remains
   the authority for whether that selection is allowed, its provider-control evidence, and its
   provider credential hydration. Local mode must not switch to unmanaged `OPENAI_API_KEY`, Tau
   credential files, or another provider-credential system.
2. Main Sequence MCP remains part of the agent's default operational tool surface. Its tools and
   resources continue to operate against the real Main Sequence platform.

The desired boundary is narrower: conversational runtime state is local and no Agent or
AgentSession is pre-created in the database, while authenticated provider and MCP services remain
remote.

## Terminology

In this ADR, **local mode** means:

- the Tau process runs in the current project workspace;
- the process owns local, durable session, A2A Task, and runtime state;
- no Main Sequence Agent or AgentSession is required or implicitly created; and
- Main Sequence authentication, provider credential hydration, and MCP remain active.

It does not mean:

- offline execution;
- a mock Main Sequence platform;
- local model-provider credentials;
- disabled Main Sequence MCP;
- a second deployable runtime role; or
- eventual synchronization of local sessions into production sessions.

## Decision

### 1. Add one explicit local-mode switch

The canonical switch is:

```env
TAU_LOCAL_MODE=true
TAU_LOCAL_PROVIDER=openai
TAU_LOCAL_MODEL=gpt-5.4
# Optional:
TAU_LOCAL_THINKING=high
```

There is no second alias and no separate local-mode manifest. `false` remains the default. Managed
execution retains its current behavior.

`TAU_LOCAL_MODE` is the only mode selector. Local execution additionally requires one explicit
provider and model; thinking level is optional. These values select execution but contain no
credentials. The developer must already be authenticated through the normal Main Sequence login
flow, just as other local Main Sequence project operations require authentication. The
resulting JWTs are handed to this process through environment variables; the Main Sequence Python
package is not a runtime dependency of `ms-tau-sdk`.

The SDK fails startup when local mode is enabled and either `TAU_LOCAL_PROVIDER` or
`TAU_LOCAL_MODEL` is absent or blank. It does not guess a model, consult an Agent Card, or fall back
to a hidden user default.

`MAINSEQUENCE_TAU_STARTUP_DEPENDENCIES_ENABLED=false` is not local mode. That setting only changes
startup timing and must not be reused to select persistence or identity semantics.

### 2. Use the Main Sequence JWT contract without an SDK dependency

Managed execution continues to authenticate with:

```env
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID=...
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET=...
```

Local mode consumes the established package-independent Main Sequence JWT environment contract:

```env
MAINSEQUENCE_AUTH_MODE=jwt
MAINSEQUENCE_ACCESS_TOKEN=...
MAINSEQUENCE_REFRESH_TOKEN=...
```

These values may be populated by the normal Main Sequence authentication/export workflow, project
provisioning, or the host process. `ms-tau-sdk` reads them directly and owns a small asynchronous
JWT header provider that:

- sends the access token as a bearer credential;
- refreshes an expiring or rejected access token through the documented Main Sequence JWT refresh
  endpoint;
- updates only its in-process credential state; and
- supplies the same authenticated headers to provider hydration and Main Sequence MCP.

`ms-tau-sdk` must not depend on the Main Sequence SDK distribution (`mainsequence`), import or
dynamically import its `mainsequence` Python package, or call its CLI. It must not read the Main
Sequence CLI's private auth store. The environment variables and public HTTP refresh contract are
the integration boundary, keeping this package independently installable.

The user does not configure a second token specifically for Tau. The same normal Main Sequence
JWTs already exported for local project operations are reused. Tokens remain secret and are never
written to local runtime state, `.tau`, logs, or diagnostics.

Auth selection is fail-closed and unambiguous:

| Mode | Main Sequence authentication |
| --- | --- |
| Managed | Runtime credential ID and secret |
| Local | Access/refresh JWT environment and HTTP refresh contract |

Settings validation is mode-aware. `MAINSEQUENCE_AUTH_MODE=jwt` is accepted only with
`TAU_LOCAL_MODE=true`; managed mode continues to accept only `runtime_credential`. Local mode
requires the access/refresh JWT pair and does not require or fall back to runtime credentials.

Local mode does not silently fall back to a runtime credential when user authentication is absent,
and managed mode does not silently consume a developer's user JWT. This prevents a local process
from accidentally acting as a deployed agent, and prevents a deployment from inheriting an
interactive user's authority.

The authenticated Main Sequence principal resolved through that JWT supplies the local caller
identity used for turn provenance. Local HTTP callers are not required to manufacture gateway-only
`X-Caller-*` headers, and caller-supplied identity headers must not override the authenticated
principal.

### 3. Make selection explicit and keep provider authorization Main Sequence-owned

Local mode reads the exact provider, model, and optional thinking level from its local execution
settings. It requests a credential for that exact provider/model pair from Main Sequence. Main
Sequence validates the selection against its provider catalog, product policy, authenticated-user
ownership, credential state, and provider-control contract before returning any credential.

The SDK then uses the same `ProviderFactory` evidence validation and in-memory provider
construction as managed execution. An environment value selects a provider or model; it does not
authorize it.

The authentication and execution credentials have different purposes:

```text
Main Sequence user JWT from the established environment contract
  + TAU_LOCAL_PROVIDER and TAU_LOCAL_MODEL
  -> authenticate an exact provider-control and hydration request
  -> receive authorized provider-control evidence and a short-lived provider credential
  -> call the selected model provider with the hydrated provider credential
```

The Main Sequence JWT is not sent to OpenAI, Anthropic, or another model provider. The hydrated
provider credential is not written to the local session store, `.tau`, environment overlays,
snapshots, logs, or errors.

Local mode must not fall back to:

- Tau's user-level provider credential store;
- provider API keys from the project environment;
- a hard-coded provider or model;
- an SDK-owned provider catalog; or
- an unvalidated custom endpoint.

Provider-control evidence continues to be intersected with Tau's installed execution
capabilities. Credential refresh continues through Main Sequence.

Because the current hydration operation requires exactly one `agent_uid` or
`agent_session_uid`, implementation requires a user-scoped hydration contract that accepts the
explicit execution selection, derives credential ownership from the authenticated user, and
returns the existing provider-control and credential envelope without creating either database
object. Conceptually, the request is:

```json
{
  "providers": ["openai"],
  "execution_selection": {
    "provider": "openai",
    "model": "gpt-5.4"
  },
  "supported_provider_control_schema_versions": [1],
  "holder_id": "local-development"
}
```

The normal Main Sequence JWT supplies the user identity. No `agent_uid` or `agent_session_uid` is
sent. The TAU SDK consumes this contract directly through the authenticated HTTP API; it does not
fake an Agent UID or AgentSession UID.

This repository does not own or modify the Main Sequence backend. Any corresponding backend API
work is an external prerequisite governed in its owning repository. No corresponding Python SDK
package dependency is introduced here.

### 4. Persist runtime session state locally

In local mode the following state is local-only:

- local session descriptors and model selection metadata;
- Tau session entries and atomic turn boundaries;
- runtime activity and cancellation state;
- resume snapshots;
- idempotency and sequence metadata;
- A2A Tasks, attempts, requester/responder messages, artifacts, events, interruption state, and
  cancellation;
- local lease/ownership coordination when more than one process opens the same session.

The SDK must not call the AgentSession bootstrap, history, append, lease, runtime-state, snapshot,
or platform task-persistence endpoints in local mode. This prohibition applies to remote platform
state; it does not prohibit the local A2A Task aggregate required by this ADR.

The default store is a schema-versioned SQLite database outside the repository, scoped by the
canonical workspace path:

```text
~/.tau/mainsequence/<workspace-hash>/runtime.sqlite3
```

The store uses transactions for ordered entry batches and turn commits. It must support clean
restart recovery and must not place mutable state inside the project's tracked `.tau` directory.
Provider credentials, Main Sequence access tokens, MCP payloads, and arbitrary tool results are not
stored unless they are already part of the normal Tau conversation record.

Local state is a separate namespace. A local session ID must never be interpreted as a backend
AgentSession UID. Turning local mode off must not upload, migrate, attach, or replay local history
into a managed session. Export/import, if later required, needs a separate decision and explicit
user action.

### 4a. Persist local operational logs

`TAU_LOCAL_MODE=true` also requires one append-only JSON Lines operational log at
`~/.tau/mainsequence/<workspace-hash>/logs/tau.jsonl`. `TAU_LOCAL_STATE_ROOT` moves the parent of
both this file and `runtime.sqlite3`; the log is not stored in the tracked project or `.tau`.
Startup creates the file before serving requests. Failure to create or open it is a startup error,
not a silent downgrade to console-only logging. Managed mode does not create a local file.

The file receives the same structured, redacted SDK, request, runtime, and propagating Python
logging events as the existing sinks. Console output remains available independently. The file is
private to the local user (`0700` log directory, `0600` files), rotated at 10 MiB with five backup
files, and safe for concurrent local processes in the same workspace. A runtime write failure must
not abort an agent turn. The SDK does not claim to capture arbitrary `print()` calls, direct
subprocess output, or project loggers that deliberately bypass Python logging propagation.

Persistent diagnostics must not contain JWTs, provider credentials, prompts, tool arguments or
results, MCP payloads, or raw exception messages. Exceptions retain their type and bounded
traceback locations, without exception message text or local variables. The log is local-only; it
is not attached, uploaded, or synchronized to any Main Sequence AgentSession. Developers are
responsible for retention and removal of their local state directory.

### 5. Create local sessions lazily

Local mode synthesizes a local agent descriptor in memory and creates a local session on first use.
Neither object is registered with Main Sequence.

For chat requests:

- an unknown supplied `sessionUid` creates a new local session;
- a repeated `sessionUid` resumes the corresponding local session; and
- an omitted `sessionUid` resolves to the workspace's `local-default` session.

The response exposes the effective local session ID so clients can resume it. Local IDs are
workspace-scoped and use a reserved local namespace so that they cannot be confused with backend
UIDs.

Managed mode keeps the existing strict contract: `sessionUid` is required and must identify an
existing backend AgentSession. No implicit backend session creation is added.

The local session obtains its initial provider/model/thinking selection from the explicit local
execution settings. Non-secret effective selection metadata is persisted locally so
`/api/chat/session-model` and restart recovery remain deterministic. If the process restarts with a
different provider or model for an existing local session, loading fails with an explicit selection
conflict; it does not silently change the session's model. Every provider construction and
credential refresh is still authorized by current Main Sequence evidence.

### 6. Keep Main Sequence MCP enabled

Local mode opens Main Sequence MCP using the same normal Main Sequence user-auth context. It lists
and projects the authenticated user's real MCP tools and resources into the Tau session exactly as
managed mode does.

The default local tool composition remains:

```text
Tau core coding tools
+ authenticated Main Sequence MCP tools and resources
+ protocol-required task controls where the route is locally supported
+ project .tau extensions
```

Local mode does not mock, cache, replace, or remove Main Sequence MCP. MCP tool calls may read or
mutate real Main Sequence resources according to their schemas and server-side permissions. Those
intentional platform operations are not runtime-session persistence and are not prevented by local
mode.

Repository-owned Tau configuration may filter or replace the effective tool composition using
Tau's normal configuration and extension mechanisms. Local mode introduces no separate MCP switch
or SDK-specific tool manifest.

The SDK must not fabricate a backend lease or caller AgentSession proof for an MCP operation. A
tool that the server declares to require a real AgentSession must either support authenticated-user
semantics in its own backend contract or return a typed authorization/capability failure. The rest
of the MCP catalog remains available. This limitation must be visible in diagnostics and must not
cause the SDK to create an AgentSession as a workaround.

The canonical outbound `a2a.send_message` tool remains present in local mode. Authenticated-user
Message delivery and Task delivery with polling do not require a local platform AgentSession and
must be supported by the platform contract. `resume_caller` remains unavailable unless the
platform gains an explicit callback contract for an unregistered local process; local development
must use polling instead of fabricating caller-session proof.

### 7. Separate local state from remote operational services

The implementation must not model local mode as a replacement for every method on
`MainSequenceClient`. Provider hydration and MCP deliberately remain remote. Instead, the runtime
composition is split along the actual ownership boundary:

```text
                         +-----------------------------+
                         | Authenticated Main Sequence |
                         | services                    |
                         | - provider evidence         |
                         | - credential hydration      |
                         | - MCP tools/resources       |
                         +--------------+--------------+
                                        |
                                        v
+------------------+       +------------+-------------+
| HTTP/SSE/A2A     +------>| Shared Tau runtime       |
| transports       |       | and ProviderFactory      |
+------------------+       +------------+-------------+
                                        |
                         +--------------+--------------+
                         | Runtime state store         |
                         +--------------+--------------+
                                        |
                          managed ------+------ local
                              backend          SQLite
```

The required abstractions are:

- an authentication/header provider usable by both runtime credentials and the standalone JWT
  environment contract;
- a provider-execution source for selection, provider-control evidence, hydration, and refresh;
- a runtime-state store for sessions, history, leases, snapshots, cancellation, and tasks; and
- the existing MCP transport parameterized by the authentication/header provider.

`SessionRuntimeManager`, Tau `CodingSession`, provider validation, event translation, workspace
configuration, project extensions, and lifecycle ownership remain shared. Local mode must not grow
a second agent loop or a simplified mock chat implementation.

### 8. Preserve the workspace and configuration ontology

Local mode always runs against the configured project workspace. It does not restore Astro's old
no-workspace role or a separate deployment artifact.

The repository's normal `.tau` configuration remains authoritative for instructions, skills,
hooks, extensions, and supported tool composition. Runtime state remains outside `.tau`. There is
no `.tau/local-mode.json`, separate system prompt, or second extension directory.

### 9. Define safe local HTTP behavior

Local mode is intended for a developer workstation, not public deployment. The CLI binds to
`127.0.0.1` by default when `TAU_LOCAL_MODE=true`. An explicit host override remains possible but
must emit a high-visibility warning because every accepted local request acts through the
authenticated Main Sequence user and can invoke live MCP tools.

Health and readiness report at least:

- `mode: local`;
- the workspace identity/digest without exposing an unsafe path when logs are exported;
- local store readiness;
- Main Sequence user-auth readiness;
- provider-control readiness;
- MCP connection state and catalog counts; and
- loaded local session counts.

Readiness fails when required Main Sequence authentication or MCP initialization fails. Local
session persistence being available does not make the agent ready if it cannot obtain its provider
or required operational tool surface.

### 10. Bound protocol behavior honestly

Local chat, A2A Message and Task execution, A2A streaming, task continuation, cancellation,
subscription, session-model inspection, restart recovery, and project Tau customization are
required.

The HTTP and A2A wire encoders remain shared. Public A2A Message and Task routes use a workspace-
scoped local identity and the SQLite task store. An A2A protocol Task is not a platform
`AgentTask`, and its identity, context, status, messages, artifacts, and events do not require a
registered AgentSession. Platform discovery of the local process, backend dispatch webhooks,
caller-delivery webhooks, push notifications, and any operation whose contract genuinely requires
a registered platform target remain unavailable. They must not create hidden backend records or
pretend that the local agent is discoverable by the platform.

The supported local public surface includes REST and JSON-RPC `message:send`, `message:stream`,
Message and Task response kinds, task list/get/cancel/subscribe, and Task continuation after
`input_required` or `auth_required`. The local Agent Card advertises Message, Task, and streaming
support while explicitly disabling push notifications. Task records survive process restart and
never migrate into managed mode.

The supported-route matrix is an executable contract and is documented before release. Returning
a typed unsupported response is preferable to accepting a request and losing its coordination
semantics.

## Explicit Persistence and Network Boundary

| Operation | Local mode behavior |
| --- | --- |
| Agent creation | Never called |
| AgentSession creation/bootstrap | Never called |
| Session entries and snapshots | Local SQLite |
| Activity, lease, cancellation | Local SQLite/process coordination |
| Public A2A Message and Task lifecycle | Local SQLite; REST and JSON-RPC supported |
| Local A2A Agent Card | Process-local capability description; not platform discovery |
| Backend dispatch/caller-delivery webhooks | Unsupported; require registered platform routing |
| A2A push notifications and `resume_caller` | Unsupported; polling remains supported |
| Main Sequence authentication | Remote JWT environment/HTTP contract; no SDK import |
| Provider/model selection | Explicit local non-secret settings |
| Provider authorization/control evidence | Remote, Main Sequence-owned |
| Provider credential hydration/refresh | Remote, Main Sequence-owned |
| Model inference | Remote provider using hydrated credential |
| Main Sequence MCP catalog/resource/tool calls | Remote and real |
| MCP mutations of platform resources | Allowed under authenticated user permissions |
| Upload local history when mode changes | Never |

Main Sequence may retain its normal security/audit records for authentication, provider hydration,
and MCP operations. The no-contamination guarantee applies to Agent, AgentSession, runtime history,
snapshot, lease, activity, cancellation, and local task state; it does not suppress auditing or
the intentional side effects of an MCP tool call.

## Rejected Alternatives

### Disable all backend access

Rejected. It would replace Main Sequence provider credentials with an unrelated local credential
system and remove the platform tools the agent is expected to use.

### Disable Main Sequence MCP in local mode

Rejected. MCP is an operational agent capability, not session-persistence baggage. A project may
change its effective Tau tool composition, but local mode does not make that decision.

### Create a temporary backend Agent and AgentSession automatically

Rejected. It preserves the exact platform-state contamination local mode is intended to remove and
makes cleanup, ownership, and failure behavior implicit.

### Use a fake AgentSession UID for provider hydration or MCP proof

Rejected. It weakens authorization evidence and creates identifiers that no authoritative system
can validate.

### Use Tau's local provider credential store

Rejected. It creates two credential authorities and makes local behavior diverge from deployed
Main Sequence execution.

### Depend on or dynamically import the Main Sequence Python SDK

Rejected. The normal login workflow already exposes a stable environment and HTTP boundary. A
Python package dependency would couple the TAU runtime to the SDK's unrelated ORM, CLI, scaffold,
and data tooling, enlarge the runtime dependency graph, and make authentication dependent on
private SDK implementation. Optional/dynamic importing would retain the same hidden coupling.

### Keep backend persistence but mark sessions as development sessions

Rejected. It still requires platform registration and network availability for every turn, leaves
development state in the platform, and does not provide a genuinely local runtime-state path.

### Implement a separate local agent loop

Rejected. The purpose is to test the same SDK/Tau composition used after deployment. Only identity
and runtime-state persistence change.

## Consequences

### Positive

- A developer can run the project with `TAU_LOCAL_MODE=true` without pre-creating an Agent or
  AgentSession.
- Prompt, extension, hook, tool, and repository changes can be exercised without polluting
  platform session history.
- Local execution uses an explicitly selected, Main Sequence-authorized provider and the same MCP
  capabilities as deployed execution.
- Local sessions survive process restarts and remain isolated by workspace.
- Local A2A Tasks exercise the same transport and Tau execution behavior without creating platform
  Agent or AgentSession records.
- The architecture gains explicit boundaries between runtime persistence, provider authorization,
  and operational platform tools.

### Negative

- Local mode is not offline and still depends on Main Sequence auth, provider hydration, MCP, and
  the selected model provider.
- The SDK needs a second runtime-state implementation and conformance tests for both stores.
- A user-authenticated local process has real platform authority through MCP, so loopback defaults
  and explicit diagnostics are required.
- Some platform orchestration operations cannot work without a registered Agent/AgentSession and
  must fail explicitly.
- The current provider hydration and caller-session-proof contracts require compatible user-scoped
  behavior outside this repository before local mode can be complete.

## Delivery Phases and Gates

### C0: Contract and external-capability inventory

Inventory every `MainSequenceClient` operation used by chat, sessions, tasks, responses, provider
hydration, and MCP. Classify it as local runtime state, required remote operational service, or
unsupported platform orchestration.

Verify the established JWT environment variable names, public refresh endpoint,
authenticated-user identity projection, and exact user-scoped provider credential hydration.
Record any missing external capability without modifying the backend from this repository.

Gate: release cannot be marked complete until credential hydration and refresh for an explicit
provider/model selection, authenticated user identity, and MCP authentication can be obtained
without an Agent or AgentSession UID.

### C1: Dependency inversion with no behavior change

Introduce the authentication, provider-execution, and runtime-state protocols. Adapt the current
runtime-credential client and backend persistence path to them.

Gate: all existing managed-mode contract, unit, and end-to-end tests pass unchanged, and no local
branch exists inside provider validation or Tau event translation.

### C2: Local identity and durable runtime store

Add `TAU_LOCAL_MODE`, required `TAU_LOCAL_PROVIDER` and `TAU_LOCAL_MODEL`, optional
`TAU_LOCAL_THINKING`, deterministic workspace scoping, lazy local session creation, SQLite schema
and migrations, entry batching, turn commits, snapshots, cancellation, local A2A task aggregates,
and restart recovery.

Gate: chat and A2A Message/Task flows can execute, cancel, restart, resume, list, get, and subscribe
without any AgentSession or platform task-persistence endpoint being called. A network spy proves
the exact allowed/forbidden endpoint classes.

### C3: Main Sequence user auth and provider execution

Implement the dependency-free asynchronous JWT environment adapter, authenticated local principal,
exact user-scoped credential hydration, provider-control validation, and credential refresh.

Gate: local inference requests the configured provider/model pair, uses only the resulting Main
Sequence-hydrated credential, persists no provider secret, and fails precisely when the selection
is unauthorized or user authentication is absent or expired. Package metadata and an
import-boundary test prove that the `mainsequence` distribution is neither a dependency nor an
import.

### C4: Main Sequence MCP parity

Parameterize MCP authentication, retain the full authenticated catalog, compose it with Tau and
project tools, and surface session-proof incompatibilities explicitly.

Gate: representative read-only and mutating MCP tools execute against the intended Main Sequence
environment in local mode; no Agent or AgentSession is created; project Tau configuration can
change the effective tool composition through its normal mechanisms.

### C5: Protocol surface and safety

Implement the local-mode route matrix, local provenance, loopback CLI default, health/readiness
fields, durable local A2A Message/Task behavior, and typed capability failures only for genuine
platform orchestration.

Gate: REST and JSON-RPC Message/Task requests, streaming, continuation, list/get/cancel/subscribe,
and restart persistence pass against the local store; unsupported orchestration never creates
hidden platform state; external bind warnings are tested; and managed mode retains its gateway
identity requirements.

### C6: Documentation, migration, and release

Update the README, quickstart, settings, runtime contract, troubleshooting, public API, changelog,
and examples. Document local state inspection/reset and the fact that MCP operations affect real
platform resources.

Gate: a clean Main Sequence project with exported Main Sequence JWT variables can install
`ms-tau-sdk`, enable local mode, select a provider/model, start `ms-tau`, run a conversation,
restart it, and resume the same local session without any database Agent or AgentSession and
without the `mainsequence` distribution installed in the runtime environment.

## Implementation Ledger

| Phase | SDK repository status | Remaining external evidence |
| --- | --- | --- |
| C0 | JWT environment names and refresh URL verified against `mainsequence-sdk`; client inventory classified | Deploy and live-test authenticated-user provider hydration without agent identity |
| C1 | Complete | None |
| C2 | Complete for local chat, session, and durable A2A Task state | None |
| C3 | Client complete; dependency/import guards and secret-persistence tests pass | Live backend authorization/hydration conformance |
| C4 | Client composition complete; no fake session proof is emitted | Live read and mutating MCP conformance under user JWT |
| C5 | Complete: local REST/JSON-RPC A2A Message/Task execution and narrow platform-only failures | None |
| C6 | SDK documentation, migration text, changelog, build, and clean test suite complete | End-to-end clean-project run against the deployed backend capability |

No Django or other backend repository is modified by this ADR's SDK implementation. The external
items above are release evidence, not authorization for this repository to change their owner.

## Verification Requirements

The implementation is not complete until automated tests prove:

1. Local startup does not require an Agent UID, AgentSession UID, runtime credential ID, or runtime
   credential secret.
2. Local startup requires valid Main Sequence access/refresh JWT environment values and refreshes
   them without importing the `mainsequence` package.
3. No Agent, AgentSession, entry, lease, activity, snapshot, cancellation, or platform
   task-persistence endpoint is called in local mode; equivalent local state remains in SQLite.
4. The exact configured provider/model pair is sent for Main Sequence authorization, evidence,
   hydration, and refresh and continues to pass the existing execution-safety validation.
5. The provider receives only its hydrated provider credential, never the Main Sequence JWT.
6. Provider credentials and Main Sequence tokens never appear in local storage or logs.
7. Main Sequence MCP connects in local mode and exposes its authenticated tools and resources.
8. Read and mutating MCP operations retain their real server-side authorization and effects.
9. Local history survives restart, preserves ordering, and is isolated by canonical workspace.
10. Concurrent local writes cannot corrupt a session or commit a turn twice.
11. An omitted local `sessionUid` resolves to `local-default`; managed mode still rejects omission.
12. Switching to managed mode never uploads or interprets local session state.
13. Managed mode still requires runtime credentials and an existing backend AgentSession.
14. Public-bind warnings and local caller-provenance rules are covered.
15. Every unsupported Agent/AgentSession-dependent route returns the documented typed error.
16. Missing provider/model settings fail before any provider or session work, and an unauthorized
    selection never falls back to another provider or credential source.
17. Built wheel metadata contains no `mainsequence` dependency, and source/import tests reject any
    `mainsequence` package import.
18. Local REST and JSON-RPC A2A Message and Task flows execute without gateway caller headers,
    canonicalize context IDs into the workspace namespace, and persist no platform session state.
19. Local task list/get/cancel/subscribe, streaming, interruption continuation, idempotent creation,
    artifacts, events, and restart recovery conform to the managed A2A wire contract.
20. `/internal/a2a` dispatch and caller-delivery hooks remain rejected in local mode, and outbound
    platform Task communication uses authenticated-user polling rather than fake caller proof.

## Acceptance Criteria

The user workflow is:

```bash
# MAINSEQUENCE_ACCESS_TOKEN and MAINSEQUENCE_REFRESH_TOKEN are already
# exported/provisioned by the project's normal Main Sequence login workflow.
export TAU_LOCAL_MODE=true
export TAU_LOCAL_PROVIDER=openai
export TAU_LOCAL_MODEL=gpt-5.4
uv run ms-tau
```

After startup:

- the project runs the real workspace-bound Tau runtime;
- no Agent or AgentSession has been registered;
- chat and public A2A Task state are durable only on the developer's machine;
- provider calls use the explicitly selected provider/model and credentials authorized and
  hydrated by Main Sequence;
- Main Sequence MCP, including authenticated-user A2A Message and polled Task communication,
  remains available and operates against the real platform; and
- removing the flag restores the existing managed AgentSession contract without migrating local
  state.
