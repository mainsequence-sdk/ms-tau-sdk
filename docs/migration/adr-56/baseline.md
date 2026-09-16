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

### C4 gate

`tests/fixtures/sdk-consumer-project` is an independent Python project with its own Python pin,
`pyproject.toml`, and `uv.lock`. It declares `ms-tau-sdk==0.1.0`, installs the SDK into the project
environment, exposes only this shim, and owns its `.tau` configuration and extension:

```python
from ms_tau_sdk import create_app

app = create_app()
```

Gate evidence:

```text
uv sync --project tests/fixtures/sdk-consumer-project --frozen
Installed ms-tau-sdk==0.1.0 and the fixture project

uv run --frozen python -c <load api.tau.main>
Main Sequence TAU SDK 0.1.0 <fixture workspace>

fixture-native Tau discovery:
extension_names = ("import_fixture",)
custom_prompt_path = <fixture>/.tau/SYSTEM.md

fixture .venv/bin/ms-tau
GET /health: 200, runtime=tau, version=0.1.0
SIGINT shutdown: clean

.venv/bin/pytest -q
268 passed, 1 skipped

.venv/bin/ruff check src tests
All checks passed!

.venv/bin/mypy src/ms_tau_sdk
Success: no issues found in 49 source files
```

The existing SDK contract suite supplies the auth-client, provider, durable/sessionless execution,
MCP, transport, persistence, cancellation, and shutdown coverage; the consuming project writes no
replacement integration layer for those capabilities.

### C5, A1–A4, T1–T5, and D1–D5 gates

The final production package tree is only `src/ms_tau_sdk`. Migration-numbered test modules and
runtime constants were renamed around supported SDK behavior. The active decision set is ADRs 0001
through 0004; earlier decisions and documents were moved under a non-normative historical archive.

The new documentation root covers installation, public API, settings, credentials, project Tau
configuration, extension ownership, runtime/HTTP behavior, repository structure, testing,
compatibility, scope, troubleshooting, and the explicit migration mapping.

Active source and tests contain no retired import or command invocation. Historical terms appear
only in the transition ADR, migration evidence/guide, and `docs/history/astro` archive.

```text
.venv/bin/pytest -q
270 passed, 1 skipped

.venv/bin/ruff format --check src tests
91 files already formatted

.venv/bin/ruff check src tests
All checks passed!

.venv/bin/mypy src/ms_tau_sdk
Success: no issues found in 49 source files
```

### C6 gate

The SDK now builds a deliberately narrow Python release bundle. Artifact verification checks the
package identity, metadata, dependency count and exact Tau pin, console entry point, required Tau
resources, and allowlisted wheel/sdist paths. It generates `DEPENDENCIES.json`, `PROVENANCE.json`,
and `SHA256SUMS`. CI adds GitHub artifact provenance, generates PEP 740 attestations in the isolated
publish job, and keeps PyPI publication behind an exact version tag and protected environment.

The clean-install verifier created a separate Python 3.13 environment and empty project workspace,
installed only the built wheel and its declared dependencies, confirmed that the installed module
came from that environment and that `astro` could not be imported, started installed `ms-tau`,
received successful health/version responses, and observed clean SIGINT shutdown.

```text
uv build --no-sources --out-dir /tmp/ms-tau-sdk-c6.TyT5bu
Successfully built ms_tau_sdk-0.1.0.tar.gz
Successfully built ms_tau_sdk-0.1.0-py3-none-any.whl

uv run python scripts/verify_distribution.py --dist-dir <candidate> --write-release-metadata
verified ms_tau_sdk-0.1.0-py3-none-any.whl and ms_tau_sdk-0.1.0.tar.gz

uv run python scripts/verify_clean_install.py <candidate wheel>
clean install verified: 0.1.0 from ms_tau_sdk-0.1.0-py3-none-any.whl

uv run pytest
272 passed, 1 skipped

uv run ruff format --check src tests scripts
94 files already formatted

uv run ruff check src tests scripts
All checks passed!

uv run mypy
Success: no issues found in 49 source files
```

### C7 gate

The reviewed stable surface is documented in the public API, compatibility, ownership, settings,
runtime, configuration, testing, and release references. Root and independent consumer locks both
resolve `ms-tau-sdk==1.0.0`. The consumer fixture imports the installed version from its own
environment and constructs the same application from its project workspace.

The final artifacts were built without project-only source overrides. The same allowlist and
metadata verifier accepted the wheel and sdist, and the clean-install verifier installed and ran
the wheel from a separate environment/workspace with no checkout on `PYTHONPATH`.

```text
uv sync --project tests/fixtures/sdk-consumer-project --frozen
Updated ms-tau-sdk==0.1.0 to ms-tau-sdk==1.0.0
fixture app: Main Sequence TAU SDK, workspace=<fixture>

uv build --no-sources --out-dir /tmp/ms-tau-sdk-c7.gwBE77
Successfully built ms_tau_sdk-1.0.0.tar.gz
Successfully built ms_tau_sdk-1.0.0-py3-none-any.whl

uv run python scripts/verify_distribution.py --dist-dir <stable> --write-release-metadata
verified ms_tau_sdk-1.0.0-py3-none-any.whl and ms_tau_sdk-1.0.0.tar.gz

uv run python scripts/verify_clean_install.py <stable wheel>
clean install verified: 1.0.0 from ms_tau_sdk-1.0.0-py3-none-any.whl

uv run pytest --cov=ms_tau_sdk --cov-branch --cov-report=term-missing:skip-covered
272 passed, 1 skipped
Total coverage: 81.55% (required: 74%)

uv run ruff format --check src tests scripts
94 files already formatted

uv run ruff check src tests scripts
All checks passed!

uv run mypy
Success: no issues found in 49 source files
```

No tag was created, no branch was pushed, and no registry was mutated by the implementation. The
protected release workflow performs those external operations only after a release owner explicitly
creates and pushes the exact `v1.0.0` tag.

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
