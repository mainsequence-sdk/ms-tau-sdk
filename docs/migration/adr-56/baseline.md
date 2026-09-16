# ADR 56 Baseline Evidence

Captured: 2026-09-16

## Source State

```text
repository: astro-tau
branch: development
commit: 9b7a1c5
tag: v4.0.25
package: mainsequence-astro 4.0.25
import: astro
entrypoint: astro-stream -> astro.app:main
Tau dependency: tau-ai==0.3.1
```

The ADR 56 document and migration ledgers are uncommitted changes on top of this baseline. No source
runtime behavior had been changed when the checks below were captured.

## Quality Baseline

### Tests

Command:

```text
.venv/bin/pytest -q -rs
```

Result:

```text
271 passed, 1 skipped in 0.71s
```

Skip:

```text
tests/e2e/test_real_conversation.py:330
ASTRO_REAL_CONVERSATION_SESSION_UID is required
```

### Lint

Command:

```text
.venv/bin/ruff check .
```

Result:

```text
All checks passed!
```

### Types

Command:

```text
.venv/bin/mypy
```

Result:

```text
Success: no issues found in 46 source files
```

### Migration-foundation verification

After adding the ADR 56 HTTP-operation compatibility contract:

```text
.venv/bin/pytest -q
272 passed, 1 skipped in 0.76s

.venv/bin/ruff check .
All checks passed!

.venv/bin/mypy
Success: no issues found in 46 source files
```

After deleting the repository-owned container/deployment artifacts and their deployment-only
tests, and removing Compose control from the opt-in conversation test:

```text
.venv/bin/pytest -q
259 passed, 1 skipped in 0.72s

.venv/bin/ruff check .
All checks passed!

.venv/bin/mypy
Success: no issues found in 46 source files
```

The lower test count is the explicit deletion of Dockerfile, overlay, Compose, Kubernetes, image
publication, and container-identity assertions. No Python SDK behavior test was removed for the
count itself.

### C1 gate

The application now constructs an explicit `ApplicationServices` graph without import-time
application creation. That graph owns authentication, the Main Sequence client, provider factory,
durable runtime manager, startup, and shutdown. The CLI runs a constructed ASGI instance, and
sessionless `AgentHarness` construction has its own boundary.

```text
.venv/bin/pytest -q
261 passed, 1 skipped

.venv/bin/ruff check .
All checks passed!

.venv/bin/ruff format --check src tests
87 files already formatted

.venv/bin/mypy
Success: no issues found in 49 source files
```

### C2 gate

Tau is upgraded to `tau-ai==0.4.2`. Packaged behavior is now a Tau-native `SYSTEM.md`; a project
`.tau/SYSTEM.md` replaces it through Tau's resolver, while project `APPEND_SYSTEM.md`, skills,
prompt templates, hooks, extensions, diagnostics, reload, and shutdown remain native Tau features.
The SDK always trusts and enables the workspace's project resources because the consuming project
already owns and runs its code. The former deployment opt-in and child-prompt branch are removed.

Tau 0.4.2 also makes atomic `SessionStorage.append_batch` part of its storage contract. The backend
storage adapter now maps that transaction to one existing batch-append request.

```text
.venv/bin/pytest -q
264 passed, 1 skipped

.venv/bin/ruff check src tests
All checks passed!

.venv/bin/ruff format --check src tests
86 files already formatted

.venv/bin/mypy src/astro
Success: no issues found in 49 source files
```

### C3 gate

The source package, distribution, and command are now `ms_tau_sdk`, `ms-tau-sdk==0.1.0`, and
`ms-tau`. `TauSDKSettings.workspace` defaults to the current directory and rejects a missing or
non-directory workspace. All SDK-specific environment settings use `MAINSEQUENCE_TAU_*`; the two
runtime credential names remain unchanged. The public package exports `create_app` and
`TauSDKSettings`.

```text
uv build
Successfully built dist/ms_tau_sdk-0.1.0.tar.gz
Successfully built dist/ms_tau_sdk-0.1.0-py3-none-any.whl

clean virtual environment:
ms-tau-sdk==0.1.0 installed from the wheel
import ms_tau_sdk: success
find_spec("astro"): none
GET /health: 200, runtime=tau, version=0.1.0
GET /version: 200, runtime=tau, version=0.1.0
SIGINT shutdown: clean
```

The wheel contains the `ms_tau_sdk` package and its Tau markdown resources. It contains no retired
namespace, Docker, Compose, Kubernetes, image, overlay, or wheelhouse artifact.

```text
.venv/bin/pytest -q
266 passed, 1 skipped

.venv/bin/ruff check src tests
All checks passed!

.venv/bin/ruff format --check src tests
85 files already formatted

.venv/bin/mypy src/ms_tau_sdk
Success: no issues found in 49 source files
```

## Current Runtime Characterization

### OpenAPI surface

Canonical JSON settings: sorted keys with compact separators.

```text
sha256: 78b7c3fdf93af5a96c130395ef4c9372346032ad359693c88cc3ca496344eaa9
title: Main Sequence Astro
version: 4.0.25
```

Current operations:

```text
POST /api/a2a/rpc
GET /api/a2a/v1/extendedAgentCard
POST /api/a2a/v1/message:send
POST /api/a2a/v1/message:stream
GET /api/a2a/v1/tasks
GET /api/a2a/v1/tasks/{task_id}
GET /api/a2a/v1/tasks/{task_id}/pushNotificationConfigs
POST /api/a2a/v1/tasks/{task_id}/pushNotificationConfigs
DELETE /api/a2a/v1/tasks/{task_id}/pushNotificationConfigs/{config_id}
GET /api/a2a/v1/tasks/{task_id}/pushNotificationConfigs/{config_id}
POST /api/a2a/v1/tasks/{task_id}:cancel
GET /api/a2a/v1/tasks/{task_id}:subscribe
POST /api/agents/{agent_uid}/responses
POST /api/agents/{agent_uid}/responses/stream
GET /api/chat
POST /api/chat
POST /api/chat/mock
GET /api/chat/session-model
POST /api/chat/session/cancel
GET /health
POST /internal/a2a/caller-deliveries:available
POST /internal/a2a/dispatches:available
GET /ready
GET /version
```

The product title and version are expected to change. Route removal or semantic change requires an
explicit disposition or separate ADR rather than accidental drift during the package refactor.

### A2A fixture hashes

```text
ca2cac7a461ba53481eea59c6fa1c6ece77e41686b59b1778c50c1cd92eb2b25  json-rpc-send-message-request.json
a9253d1ddfe2819ab9c5e5e9e2a25fb3514768493238b393777a18891a735f3e  json-rpc-send-message-response.json
a1d89f8d934424ac4278d44ead8da14f08b84aea7e79c729051ccde74db12e78  message-send-request.json
1efb1f8da3273aa04730cf6230959567f3133234f9feb0d612b40e8187071ee2  message-send-response.json
36c736bb316700397905a4b9ebbc5e1b028be7bc99678f615b1027af9e8d03c1  task-response.json
```

### Process entrypoint

`astro-stream` invokes `astro.app:main`, which runs Uvicorn over the packaged FastAPI application.
Application startup validates runtime credentials, creates the Main Sequence backend/auth client,
provider factory, and `SessionRuntimeManager`, connects startup dependencies, and exposes health,
chat, responses, A2A, and sessions routers.

### Durable Tau path

```text
request with session uid
  -> SessionRuntimeManager.prompt
  -> backend bootstrap + lease + provider evidence + history/snapshot
  -> CodingSession.load(CodingSessionConfig)
  -> CodingSession.prompt
  -> translated Tau events
  -> HTTP/A2A streaming adapter
  -> batched persistence and turn settlement
```

The current tool composition is Tau coding tools, Main Sequence MCP tools, and protocol-required
task-control tools. The current package injects its prompt resources programmatically and enables
project `.tau/extensions` only when `ASTRO_CODE_REPOSITORY_EXTENSIONS_ENABLED=true`.

### Sessionless Tau path

```text
agent response request
  -> provider credential hydration
  -> direct AgentHarness construction with tools=[]
  -> AgentHarness.prompt or prompt_message
  -> final AssistantMessage
```

`POST /api/agents/{agent_uid}/responses/stream` currently uses SSE framing but buffers execution and
emits one final event; it is not incremental Tau-event streaming.

### Authentication

`MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` and `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET` are exchanged for
a short-lived bearer access token used by the Main Sequence backend client.

## Inventory Counts

At baseline, excluding virtual-environment and bytecode artifacts:

```text
Python source files under src/astro: 46
Python test/fixture files under tests: 34
Markdown files under docs: 66 before adding this migration workspace
Active ADRs before ADR 56: 9 (ADR 47 through ADR 55)
```

## Baseline Gaps Still Required for C0

- Capture representative assistant-ui and A2A streaming event sequences as implementation-neutral
  fixtures; OpenAPI operations and existing A2A fixture hashes are now recorded above.
- Run the credential-gated real-conversation test in an authorized environment.
- Complete candidate Python SDK artifact ownership and disposition.

External repository implementation, service deployment, data migration, and operational inventory
are outside ADR 56 and are not SDK phase gates.
