# ADR 0008: Tau Board as a Separate Local Development Companion

Status: Proposed

Date: 2026-09-21

## Context

`ms-tau-sdk` is a headless, workspace-bound agent runtime. Local mode already exposes chat,
public A2A Message and Task operations, health, and durable local state. Developers still have to
assemble HTTP requests, inspect SQLite, and tail JSON Lines logs to interact with and understand a
local run. A small browser dashboard would make these operations accessible without using the Main
Sequence platform UI.

The dashboard must have the same relationship to Tau that TensorBoard has to a training runtime:
it is an optional, separately running development tool. It must not become part of Tau's FastAPI
application, agent loop, or core dependency set. A broader production or cross-agent console may
be designed later; it does not determine this local tool's architecture.

## Decision

### 1. Publish a separate distribution

Create a separate Python project under `packages/tau-board/`, outside `src/ms_tau_sdk/`, with its
own package metadata, tests, static assets, and `tau-board` command. Its distribution name is
`ms-tau-board`. It runs its own loopback HTTP server to serve the UI and connect to an already
running Tau HTTP endpoint. The board does not import `ms_tau_sdk` internals, embed the Tau ASGI app,
or own an agent runtime. Its integration contracts are the documented HTTP API, local-state layout,
and structured log format.

`ms-tau-sdk` retains no board Python modules, UI assets, routes, or board runtime dependencies. The
only permitted core-distribution change is optional-dependency metadata so users can install:

```bash
uv add 'ms-tau-sdk[tau-board]'
```

The `tau-board` extra depends on a compatible published `ms-tau-board` release. Installing plain
`ms-tau-sdk` never installs or starts the board. `ms-tau-board` can also be installed and updated
independently. The board must not depend back on `ms-tau-sdk`, avoiding a dependency cycle; the
extra itself already installs both distributions. The two distributions need separate build and
release checks, and the extra must reference a board version that is published before an SDK
release advertising it. The root wheel and source distribution must not include board source or
assets.

### 2. Make endpoint and local-state location selectable

The board presents two editable connection settings:

| Setting | Environment default | UI override |
| --- | --- | --- |
| Tau base URL, for example `http://127.0.0.1:8010` | `TAU_BOARD_TAU_URL`, otherwise `http://127.0.0.1:8787` | Enter or select another URL, then probe `/health` and `/ready` |
| Workspace-specific local state directory containing `runtime.sqlite3` and `logs/tau.jsonl` | `TAU_BOARD_STATE_DIR`; otherwise derive from `TAU_LOCAL_STATE_ROOT` (or its documented default) and the connected runtime's `workspace_digest` | Enter another directory and inspect its database and logs |

An explicit UI value takes precedence over the environment default for the current board session.
The board shows the effective URL, state directory, runtime mode, workspace digest, and connection
status. It never assumes that a directory selected for one endpoint belongs to another; a digest
mismatch is shown before displaying state. An absent database or log file is a normal empty state
because local SQLite is created lazily.

The board may read the same environment or `.env` file as a local project, but it reads only the
settings needed for these defaults and local runtime profiles. Main Sequence JWTs and runtime
credentials remain in the Tau process and are never returned to browser code, saved in board
preferences, or printed in board diagnostics. The board does not perform Main Sequence login or
provider credential hydration.

The initial release accepts HTTP loopback Tau endpoints. The board itself binds to loopback. A
non-loopback or production endpoint requires a later decision on authentication, platform routing,
and access control; the URL field does not imply those capabilities.

### 3. Provide a compact interaction surface

The main view has a conversation composer and a Task composer:

- Chat uses `POST /api/chat`, renders the existing assistant-ui SSE stream, displays tool activity
  when present, retains the returned `X-Agent-Session-Uid`, and allows cancellation through the
  existing session route.
- A2A offers Message and Task actions. It builds valid message/context IDs and parts, uses the
  advertised response-kind extension when requesting a Task, displays status and artifacts, and
  supports list, get, subscribe, cancel, and continuation when input is required.
- Every action shows the selected endpoint profile and provider, model, and thinking selection
  before submission. Failures display the runtime's safe error message and relevant identifiers.

The UI must not invent new wire semantics or write Task records directly to SQLite. All actions
that change a session or Task go through Tau's public HTTP routes. The first version handles text
input; richer file and structured-output composition can be added after the basic flows work.

### 4. Define honest provider, model, and thinking selection

Local Tau currently fixes `TAU_LOCAL_PROVIDER`, `TAU_LOCAL_MODEL`, and `TAU_LOCAL_THINKING` at
process startup. Its chat and A2A requests do not contain effective per-turn selection fields, and
an existing local session cannot silently change its persisted selection. The board therefore
must not present arbitrary values as if one running endpoint could execute them.

For this ADR, an **endpoint profile** is a user-configured Tau URL plus its provider, model, and
thinking metadata. The board allows selecting a profile for each chat or A2A action and routes that
action to the profile's endpoint. Several local Tau processes with different environment settings
can provide several choices. Session and Task identifiers remain associated with the profile that
created them; changing profile starts or selects a compatible session instead of replaying a turn
under a different model. Where an existing session is available, `/api/chat/session-model` is used
to check the declared selection before continuing it. For an endpoint the board did not start,
profile metadata is user-declared and shown as unverified until Tau confirms an existing or newly
created session's selection. The board must show the effective selection returned by Tau after the
first action and stop reuse of a mismatched profile.

With only one attached Tau endpoint, the selector has only that endpoint's configured selection.
Arbitrary provider/model/thinking switching inside a single running Tau process is not part of this
board decision. If needed later, it requires a separate runtime protocol decision and SDK change,
including authorization, session persistence, and compatibility rules. The board must clearly
report an unavailable selection rather than send ignored request fields.

### 5. Explore the local database without changing it

The board opens the selected `runtime.sqlite3` in SQLite read-only mode. It provides bounded,
paginated views of sessions, ordered entries, Tasks, Task messages, artifacts, attempts, events,
and snapshot metadata, with links from a session or Task to the relevant interaction view. A row
detail view may show the stored JSON so a developer can understand an individual record.

Database exploration never executes writes, migrations, `VACUUM`, schema changes, or arbitrary SQL
from the browser. The board uses short-lived read transactions so Tau can continue writing through
WAL. It checks the database schema version and shows an explicit compatibility error for an
unknown version. SQLite is an inspection source only: chat, continuation, and cancellation still
use HTTP. There is no local-to-platform import or synchronization path.

Conversation entries and tool records can contain sensitive project content. The UI shows this
content only on demand, does not upload it, and does not place row bodies in board logs or browser
persistent storage. Board UI preferences may save endpoint and directory paths, but never tokens,
conversation bodies, Task artifacts, or log contents.

### 6. Read local operational logs

The board tails the selected `logs/tau.jsonl` and its rotated files with bounded reads. It shows
recent events and filters for level, time, session, Task, and event name when those fields exist.
It handles a missing file, partial last line, and rotation without treating them as Tau failures.
It displays the SDK's already filtered structured events; it does not turn on payload logging,
reconstruct prompts from logs, or upload logs. A log detail view can show one full structured
event, subject to the same local-only handling as database rows.

### 7. Keep the local trust boundary visible

The board's server listens on `127.0.0.1` by default and uses a same-origin browser UI with a
small server-side proxy for the selected Tau endpoint. The proxy accepts only the required Tau
routes, validates the selected loopback URL, and does not expose arbitrary filesystem paths or a
general HTTP forwarding service. File reads are limited to the selected state directory and the
expected database and log files. The UI identifies that Tau's MCP tools may operate on real Main
Sequence resources even while sessions and Tasks remain local.

## Scope and consequences

The initial board is a local development and inspection tool for one or more explicitly configured
Tau endpoint profiles. It is not a deployment control plane, platform Agent directory, credential
manager, database editor, generic SQLite client, or production observability service. It does not
add endpoints to `ms-tau-sdk` or promise that local state can be attached to a managed AgentSession.

This design keeps the runtime package small and independently usable. It also means the first
board version cannot recover a full chat history through a stable Tau HTTP API; the database view
provides local inspection, while the live chat view remembers the session it started. Direct
SQLite inspection and the documented JSON Lines format are intentionally local integrations and
must be checked against each compatible SDK release.

## Verification criteria

1. Plain `ms-tau-sdk` installation contains no board dependency, assets, import, route, or startup
   behavior. The optional extra installs a published, compatible `ms-tau-board` distribution.
2. A clean board install starts independently and connects to a Tau process at an env-default or
   UI-overridden loopback URL, including a non-default port such as `8010`.
3. The state-directory override changes only board inspection. It can show sessions, entries,
   Tasks, and logs from the chosen directory without modifying SQLite or log files.
4. Chat and A2A Message/Task flows work through the existing public HTTP contract, including
   streaming, Task continuation, cancellation, and reconnect after a browser refresh.
5. Profile selection routes each action to the selected Tau endpoint and never claims that an
   unsupported provider/model/thinking value was applied.
6. Credentials and row/log contents are absent from board preferences and board logs. The board
   cannot proxy to non-loopback hosts or read outside the selected state directory.
7. Missing files, unknown SQLite schema versions, a stopped Tau endpoint, SSE interruption, and
   log rotation produce actionable UI states without corrupting local runtime state.

## Alternatives considered

Embedding routes or static assets in `ms-tau-sdk` would couple the headless runtime to an optional
development interface and expand its release surface. Direct browser calls to Tau would require
cross-origin configuration and would make file inspection unavailable without another process.
A general database editor would create a write path around Tau's leases, task lifecycle, and
session persistence rules. These alternatives are rejected for the local board.
