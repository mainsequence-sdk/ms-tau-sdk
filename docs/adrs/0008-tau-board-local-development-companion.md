# ADR 0008: Tau Board as a Separate Local Development Companion

Status: Accepted

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

The package layout is fixed at this level:

```text
packages/tau-board/
├── pyproject.toml
├── src/ms_tau_board/
│   ├── cli.py
│   ├── app.py
│   ├── config.py
│   ├── env_file.py
│   ├── proxy.py
│   ├── state.py
│   ├── logs.py
│   └── static/
│       ├── index.html
│       ├── app.js
│       ├── board.css
│       ├── bulma.min.css
│       └── BULMA-LICENSE.txt
└── tests/
```

### 2. Fix the implementation stack and size boundary

The UI is one static `index.html` using **Bulma 1.0.4 CSS** for layout, forms, tables, tabs,
panels, and status badges. Package the prebuilt `bulma.min.css` and its license inside the board
wheel. Use one small board stylesheet for project-specific layout. Do not load Bulma or any other
asset from a CDN. Bulma is CSS-only and publishes a single ready-to-use stylesheet; no Bulma
JavaScript, Sass compilation, icon library, or web font is needed ([Bulma overview](https://bulma.io/),
[Bulma installation](https://bulma.io/documentation/start/installation/)). Use native HTML controls
and inline text or small inline SVG where needed.

Browser behavior uses **plain JavaScript in one ES module**: `fetch`, `ReadableStream`,
`AbortController`, and DOM APIs. The module parses Tau's POST-based SSE streams; `EventSource`
cannot make the required POST requests. There is no React, Vue, Svelte, TypeScript, npm, Vite,
Webpack, Sass, client-side router, or generated frontend bundle. The HTML is static, so no template
engine is needed. The board has six Bulma tab views: Connect, Chat, A2A, State, Logs, and
Settings. Chat and A2A maintain only ephemeral page state and non-sensitive endpoint preferences.

The board server uses **Starlette + Uvicorn** and **HTTPX**. Starlette serves the packaged static
files and the board's small JSON/streaming API; HTTPX forwards only allowlisted Tau operations and
preserves SSE streaming. `sqlite3`, `json`, and file I/O come from the Python standard library.
The board's standalone distribution declares only `starlette>=1.3,<2`, `uvicorn>=0.51,<1`, and
`httpx>=0.28,<1` as runtime dependencies and requires Python 3.13 or newer. It does not depend on
FastAPI or `ms-tau-sdk`. Starlette provides static-file and streaming response primitives, and
HTTPX provides asynchronous streaming suitable for this proxy ([Starlette static files](https://www.starlette.io/staticfiles/), [Starlette responses](https://www.starlette.io/responses/), [HTTPX async streaming](https://www.python-httpx.org/async/)).

The board project uses Hatchling to build its wheel and source distribution. No Node installation
or frontend build runs at install time or in release CI. The pinned Bulma CSS is about 678 KiB raw
and 66 KiB gzip-compressed. All packaged UI assets together must stay below **800 KiB raw** and
**120 KiB gzip-compressed**; board-authored JavaScript must stay below **50 KiB raw**. A build
check measures these limits and verifies that the HTML references only packaged assets. Raising
either limit requires amending this ADR.

### 3. Make endpoint and local-state location selectable

The board presents two editable connection settings:

| Setting | Environment default | UI override |
| --- | --- | --- |
| Tau base URL, for example `http://127.0.0.1:8010` | `TAU_BOARD_TAU_URL`; otherwise use `MAINSEQUENCE_TAU_PORT` or `8787` on `127.0.0.1` | Enter or select another URL, then probe `/health` and `/ready` |
| Workspace-specific local state directory containing `runtime.sqlite3` and `logs/tau.jsonl` | `TAU_BOARD_STATE_DIR`; otherwise derive from `TAU_LOCAL_STATE_ROOT` (or its documented default) and the connected runtime's `workspace_digest` | Enter another directory and inspect its database and logs |

An explicit UI value takes precedence over the environment default for the current board session.
The board shows the effective URL, state directory, runtime mode, workspace digest, and connection
status. It never assumes that a directory selected for one endpoint belongs to another; a digest
mismatch is shown alongside inspected state. An absent database or log file is a normal empty state
because local SQLite is created lazily.

The selected URL and directory are held in board-server memory under a random, browser-session
cookie (`HttpOnly`, `SameSite=Strict`); a page refresh retains them, and a board restart resets
them to environment defaults. The cookie contains no endpoint, path, credential, or content. The
board does not write its own configuration database. Profile names and non-secret connection
defaults may be entered again or supplied through environment configuration.

The board reads a fixed allowlist of non-secret process settings for the Settings view, including
`MAINSEQUENCE_ENDPOINT`, local-mode selection, Tau bind address, workspace, state roots, and board
defaults. The view labels these as a snapshot of the **board process**, not proof of the running
Tau process's configuration. It shows only presence or absence for JWTs and runtime credentials;
their values are never returned to browser code or printed in board diagnostics. The board does not
perform Main Sequence login or provider credential hydration.

The Settings view may also read and edit the named non-secret variables in a local `.env` file.
`TAU_BOARD_ENV_FILE` selects that file; the default is `.env` in the board's working directory,
which is the repository root in the VS Code launch configuration. Edits preserve unrelated lines,
including credentials, and replace the file atomically. The browser cannot choose an arbitrary
file or edit credential fields. A saved `.env` value does not change the current process
environment: Tau settings take effect after Tau restarts, and board startup defaults after the board
restarts. The current Tau endpoint and state-directory profile overrides can also be changed in
Settings and take effect immediately in the browser session.

The initial release accepts HTTP loopback Tau endpoints. The board itself binds to loopback. A
non-loopback or production endpoint requires a later decision on authentication, platform routing,
and access control; the URL field does not imply those capabilities.

The board command binds to `127.0.0.1:8788` by default, with `TAU_BOARD_PORT` and `--port`
overrides. `--tau-url` and `--state-dir` override environment defaults at process startup. UI
overrides take precedence for the active browser session. The board does not automatically launch
or stop Tau.

### 4. Provide a compact interaction surface

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

The board server exposes a fixed, same-origin surface. `GET /` and `GET /assets/*` serve packaged
files. `GET /api/board/config` returns effective non-secret settings; `PUT /api/board/config` sets
the current UI override; `GET /api/board/settings` returns safe process and `.env` settings, and
`PUT /api/board/settings` updates only allowlisted non-secret `.env` assignments;
`GET /api/board/connection` probes Tau health and readiness. Read-only
board routes under `/api/board/state/*` provide paginated SQLite views, and
`GET /api/board/logs` provides bounded log records. `/tau/*` proxies only the existing Tau health,
chat, session-model/cancel, and public A2A Message/Task routes. It does not proxy `/internal/*`,
OpenAPI docs, arbitrary paths, or arbitrary methods. The proxy streams SSE frames without
buffering a full response and aborts the upstream request when the browser disconnects.

For mutation routes, the board checks the browser `Origin` against its own loopback origin. The
proxy never forwards browser-supplied authorization or caller-identity headers, does not follow
redirects, and accepts a destination only after its `/health` response identifies Tau in local
mode. The browser uses same-origin `/tau/*`; developers do not need to set Tau CORS origins.

### 5. Define honest provider, model, and thinking selection

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

The default profile takes its URL from the connection setting above and its selection label from
`TAU_LOCAL_PROVIDER`, `TAU_LOCAL_MODEL`, and `TAU_LOCAL_THINKING` when present. Additional profiles
come from `TAU_BOARD_PROFILES`, a JSON array of objects with exactly `name`, `url`, `provider`,
`model`, optional `thinking`, and optional `stateDir` fields, or from the Connect view for the
current browser session. Profile data contains no credentials. The action forms use a profile
picker; the provider, model, and thinking controls show and select the values available among
configured profiles. An edit to one of those values selects a matching profile or asks the user to
configure an endpoint for that combination. It does not change a running Tau process.

With only one attached Tau endpoint, the selector has only that endpoint's configured selection.
Arbitrary provider/model/thinking switching inside a single running Tau process is not part of this
board decision. If needed later, it requires a separate runtime protocol decision and SDK change,
including authorization, session persistence, and compatibility rules. The board must clearly
report an unavailable selection rather than send ignored request fields.

### 6. Explore the local database without changing it

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

### 7. Read local operational logs

The board tails the selected `logs/tau.jsonl` and its rotated files with bounded reads. It shows
recent events and filters for level, time, session, Task, and event name when those fields exist.
It handles a missing file, partial last line, and rotation without treating them as Tau failures.
It displays the SDK's already filtered structured events; it does not turn on payload logging,
reconstruct prompts from logs, or upload logs. A log detail view can show one full structured
event, subject to the same local-only handling as database rows.

### 8. Keep the local trust boundary visible

The board's server listens on `127.0.0.1` by default and uses a same-origin browser UI with a
small server-side proxy for the selected Tau endpoint. The proxy accepts only the required Tau
routes, validates the selected loopback URL, and does not expose arbitrary filesystem paths or a
general HTTP forwarding service. Inspection reads are limited to the selected state directory and
its expected database and log files. The Settings view additionally accesses its configured local
`.env` file for allowlisted assignments. The UI identifies that Tau's MCP tools may operate on real Main
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
8. The built wheel runs without Node or network-fetched UI assets, contains the pinned Bulma CSS
   and its license, and passes the static-asset and JavaScript size limits above.

## Alternatives considered

Embedding routes or static assets in `ms-tau-sdk` would couple the headless runtime to an optional
development interface and expand its release surface. Direct browser calls to Tau would require
cross-origin configuration and would make file inspection unavailable without another process.
A general database editor would create a write path around Tau's leases, task lifecycle, and
session persistence rules. These alternatives are rejected for the local board.

For the UI CSS, Bootstrap's precompiled stylesheet would work but brings a broader component
system than this board needs. [Pico CSS](https://picocss.com/docs) is smaller in concept and can be
linked directly, but its semantic, class-light design leaves the board's tabs, dense task tables,
and state explorer layout to custom CSS. [Tabler](https://docs.tabler.io/) supplies a complete
dashboard kit, but its Bootstrap-based layouts and additional assets exceed this tool's narrow
scope. Bulma supplies the required dashboard primitives as one CSS file without a frontend build
or JavaScript framework.
