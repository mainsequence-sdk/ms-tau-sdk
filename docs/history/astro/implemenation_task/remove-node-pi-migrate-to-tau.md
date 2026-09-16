# Remove Node and Pi: Migrate Astro to Tau

Status: Implemented in workspace; production cutover pending
Date: 2026-07-23
Target runtime: Python 3.13+
Tau baseline: `tau-ai==0.3.1`
Tau source baseline: `edd4ccc6171420015fa0f04bec75d38fe32beb68`

Superseded in part by ADR 55 on 2026-09-16: the migration's `tau-file-tools`,
`tau-web-access`, and model-facing `get_runtime_info` requirements were compatibility baggage and
are removed from the active Astro runtime. Tau core tools, Main Sequence MCP, A2A controls, and the
project-owned `.tau/extensions` host contract remain. The historical design below is retained to
explain commit `781ecf4`; it is not the current tool-catalog contract.

## Implementation Snapshot

The code migration is implemented across Astro, Django, and the independent
`tau-file-tools` and `tau-web-access` packages. Runtime, protocol, session,
provider, tool, image, and deployment tests are included.

The following remain release operations and cannot be completed by source
changes alone:

- publish the two independent tool wheels
- publish the Astro runtime and executor-bundle images
- take the production database snapshot and apply migrations
- deploy Django and Astro atomically
- run live provider/A2A smoke tests with production credentials
- drain old workloads and reopen traffic

## Goal

Replace the current Node.js and Pi runtime with a Python-only Astro service built on
[Hugging Face Tau](https://github.com/huggingface/tau).

The completed system must:

- run Astro and CodeRepository code in one Python 3.13+ environment
- embed Tau as a Python library instead of spawning a coding-agent CLI
- use a Python ASGI server for the existing HTTP, streaming, and A2A interfaces
- use a Python client for all Main Sequence backend communication
- persist native Tau session entries through backend APIs
- keep provider credentials backend-owned and runtime-neutral
- retain all required tools, skills, CodeRepository context, provider auth, cancellation, and streaming behavior
- remove Node.js, npm, TypeScript, Pi, `pi-web-access`, Pi overlays, Pi JSONL, Pi RPC, and the checkpoint sidecar

## Decisions

These decisions are fixed for this implementation:

1. There is no Pi session or checkpoint backward compatibility.
2. The backend may change in the same release as Astro.
3. Existing Pi sessions may be deleted, reset, or marked unusable during cutover.
4. Every supported code repository image must provide Python 3.13 or newer.
5. Astro, Tau, the Main Sequence SDK, and project dependencies run in the same Python environment.
6. There is no separate `/opt/astro` interpreter or hidden Astro virtual environment.
7. Tau is embedded through its Python APIs. Astro does not launch the `tau` CLI.
8. The final production image contains no Node executable and no npm dependencies.
9. The checkpoint sidecar is removed. Session durability becomes part of the main async service.
10. The backend is the canonical source for sessions, provider credentials, model availability,
    capabilities, A2A tasks, and runtime leases.
11. Tau implementation types do not become public Astro HTTP contracts.
12. Production cutover is atomic. There is no long-lived production mode that supports both Pi and Tau.

## Non-Goals

- Importing historical Pi JSONL sessions into Tau.
- Translating Tau entries back into Pi entries.
- Preserving Pi-specific environment variable names.
- Preserving npm package names or image target names containing `pi`.
- Keeping the current local file checkpoint bundle format.
- Running the Tau TUI inside the Astro service.
- Loading untrusted Python extensions automatically from arbitrary cloned repositories.
- Supporting code repository images with Python 3.12 or older.

## Current Architecture Being Removed

The current request path is:

```text
Node HTTP server
  -> Astro request/session orchestration
  -> spawn pi --mode json or pi --mode rpc
  -> parse Pi stdout JSON events
  -> translate events into assistant-ui or A2A output
  -> Pi writes local JSONL
  -> checkpoint sidecar watches local files
  -> sidecar flushes checkpoint bundles to the backend
```

The following mechanisms exist only because Pi is an external Node process:

- cold `pi --mode json` process launches
- warm `pi --mode rpc` process registry
- the patched `runtime_ready` RPC event
- RPC command IDs and acknowledgement timeouts
- stdout line buffering and non-JSON diagnostics
- process-level `SIGTERM` and `SIGKILL` cancellation
- Pi agent-directory materialization
- Pi settings overlays
- Pi-specific credential files
- file watches for session JSONL mutation
- a second checkpoint sidecar container

All of these are deleted rather than translated.

## Target Architecture

```text
ASGI server: FastAPI + Uvicorn
  -> HTTP authentication and request context
  -> protocol adapters
       -> assistant-ui data stream
       -> stateless LLM response
       -> A2A REST and JSON-RPC
  -> Astro session runtime manager
       -> Tau CodingSession
       -> per-session asyncio lock
       -> cancellation scope
       -> idle session eviction
  -> Astro provider factory
       -> Tau built-in providers
       -> Astro custom ModelProvider implementations
  -> Astro tools and runtime context
  -> Main Sequence async Python client
       -> runtime identity
       -> sessions and native Tau entries
       -> runtime leases
       -> provider credentials
       -> capabilities and model catalog
       -> A2A tasks
```

There is one operating-system process for the Astro service and one Python environment:

```text
python 3.13+
  astro
  tau-ai
  mainsequence
  project dependencies
```

Tau tools that run Python, package commands, tests, or project scripts see the same interpreter and
installed packages as the Astro process.

## Proposed Python Layout

Create a conventional source package:

```text
pyproject.toml
uv.lock
.python-version
src/
  astro/
    __init__.py
    app.py
    settings.py
    logging.py
    errors.py
    api/
      dependencies.py
      health.py
      chat.py
      llm.py
      models.py
      providers.py
      sessions.py
      a2a.py
    protocols/
      assistant_ui.py
      a2a.py
      a2a_models.py
      strict_json.py
    runtime/
      manager.py
      session.py
      cancellation.py
      context.py
      events.py
      leases.py
    providers/
      definitions.py
      factory.py
      credentials.py
      catalog.py
      custom/
    sessions/
      storage.py
      entries.py
      projection.py
    backend/
      protocol.py
      mainsequence.py
      auth.py
      models.py
    capabilities/
      materializer.py
    tools/
      runtime_info.py
      web_access.py
    extensions/
      telemetry.py
      project_policy.py
      skills.py
tests/
  unit/
  contract/
  integration/
  fixtures/
```

The dedicated Pi-compatible file discovery tools live in a separate Python distribution inside
the Astro monorepo:

```text
packages/
  tau-file-tools/
    pyproject.toml
    src/
      tau_file_tools/
        __init__.py
        grep.py
        find.py
        ls.py
    tests/
      test_grep.py
      test_find.py
      test_ls.py
      test_contract.py
```

This package is a root `uv` workspace member. It is not part of the `astro` Python distribution and
does not import Astro, but its source remains inside this repository. Astro consumes it as a normal
workspace dependency; a wheel can be published later for upstreaming or external consumers.

The exact module split may change during implementation, but the boundaries must remain:

- API routes do not construct providers directly.
- Tau events do not leak directly into public HTTP response models.
- Backend transport models do not leak into Tau session construction.
- Provider credentials are not stored in global process environment variables.
- Session storage does not depend on local JSONL files.

## Python Package and Tooling

Add a root `pyproject.toml` with:

- `requires-python = ">=3.13"`
- `tau-ai==0.3.1`
- `fastapi`
- `uvicorn`
- `httpx`
- `pydantic`
- `pydantic-settings`
- `anyio`
- the approved `mainsequence` SDK constraint
- the separately packaged `tau-file-tools` distribution
- the separately packaged Python/Tau web-access extension

Use:

- `uv` for locking and reproducible installation
- `pytest` and `pytest-anyio` for tests
- `ruff` for formatting and linting
- `mypy --strict` for application boundaries and transport models
- `importlib.metadata.version("astro")` for release version reporting

Pin Tau exactly in `uv.lock`. Tau is under active development and its public interfaces may move.
Tau upgrades must be explicit pull requests with the Astro runtime contract suite run against the
new version.

## Tau Provider Audit

### Confirmed Built-In Provider Catalog

Tau `v0.3.1` contains 28 provider entries:

| Provider ID | Display name or role |
| --- | --- |
| `openai` | OpenAI API |
| `openai-codex` | OpenAI Codex subscription |
| `anthropic` | Anthropic |
| `google` | Google Gemini |
| `deepseek` | DeepSeek |
| `xai` | xAI |
| `groq` | Groq |
| `cerebras` | Cerebras |
| `nvidia` | NVIDIA NIM |
| `openrouter` | OpenRouter |
| `zai` | ZAI |
| `mistral` | Mistral |
| `minimax` | MiniMax |
| `minimax-cn` | MiniMax China |
| `moonshotai` | Moonshot AI |
| `kimi-code` | Kimi Code subscription |
| `moonshotai-cn` | Moonshot AI China |
| `huggingface` | Hugging Face Inference Providers |
| `fireworks` | Fireworks |
| `together` | Together AI |
| `vercel-ai-gateway` | Vercel AI Gateway |
| `xiaomi` | Xiaomi MiMo |
| `xiaomi-token-plan-cn` | Xiaomi token plan China |
| `xiaomi-token-plan-ams` | Xiaomi token plan Amsterdam |
| `xiaomi-token-plan-sgp` | Xiaomi token plan Singapore |
| `opencode-go` | OpenCode Go |
| `opencode` | OpenCode Zen |
| `github-copilot` | GitHub Copilot |

Tau has runtime implementations for these protocol families:

| Tau protocol | Tau implementation |
| --- | --- |
| OpenAI Chat Completions | `OpenAICompatibleProvider` |
| OpenAI Responses | `OpenAICompatibleProvider` |
| Anthropic Messages | `AnthropicProvider` |
| OpenAI Codex Responses | `OpenAICodexProvider` |
| Google Generative AI | `GoogleGenerativeAIProvider` |
| Mistral Conversations | `MistralConversationsProvider` |

Provider catalog entries can select a protocol per provider and, where supported, per model.

### Confirmed Custom Provider Support

Tau supports two distinct extension paths.

#### Configuration-only providers

An endpoint using one of Tau's supported wire protocols can be added without agent-loop changes.
Astro can construct an `OpenAICompatibleProviderConfig` or equivalent configuration from backend
provider metadata.

The managed Astro service must not depend on user-level `~/.tau/catalog.toml`. The Main Sequence
backend catalog is canonical, and Astro constructs Tau provider configuration in memory.

Use this path for:

- custom OpenAI-compatible endpoints
- local OpenAI-compatible servers
- provider gateways using OpenAI Responses or Chat Completions
- Anthropic-compatible gateways
- model-specific base URLs, headers, context windows, and thinking settings supported by Tau

#### Code-level providers

Tau's public `ModelProvider` protocol requires one method:

```python
class ModelProvider(Protocol):
    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]: ...
```

`CodingSessionConfig` and `AgentHarnessConfig` accept any object implementing this protocol.
Therefore Astro can support providers that Tau's stock factory does not know about.

Use this path for:

- Amazon Bedrock
- Azure-specific OpenAI authentication or endpoint behavior
- Google Vertex AI
- Gemini CLI or account-specific protocols
- Antigravity
- future Main Sequence gateways with non-standard streaming

Important distinction:

- Tau catalog extension supports only Tau's declared provider kinds and APIs.
- Arbitrary protocols require an Astro Python provider class.
- This is still a supported integration because the agent harness depends on the public
  `ModelProvider` protocol, not on concrete Tau provider classes.

### Astro Provider Factory

Create:

```python
class AstroProviderFactory:
    async def create(
        self,
        definition: ProviderDefinition,
        credential: ProviderCredential | None,
        model: str,
        thinking_level: str | None,
    ) -> ModelProvider: ...
```

The factory must:

- route known Tau protocols to Tau provider implementations
- route custom provider kinds to Astro `ModelProvider` implementations
- resolve credentials from backend response objects
- never mutate global environment variables to scope a request
- apply provider-specific headers and base URLs
- validate the requested model against the backend catalog
- normalize context windows and thinking levels
- support cancellation through Tau's `CancellationToken`
- close provider-owned `httpx.AsyncClient` instances during session eviction
- redact credentials from exceptions and logs

### Provider Acceptance Matrix

Before production cutover, classify every provider currently exposed by the Main Sequence backend:

| Classification | Required implementation |
| --- | --- |
| Tau built-in and same protocol | Use Tau implementation |
| Not built-in but OpenAI-compatible | Backend definition plus Tau compatible implementation |
| Anthropic-compatible | Backend definition plus Tau Anthropic implementation |
| Google Generative AI | Tau Google implementation |
| Mistral Conversations | Tau Mistral implementation |
| Non-standard protocol | Astro custom `ModelProvider` |
| Unsupported and not required | Remove from backend catalog before cutover |

Every provider left in the backend catalog must have:

- a factory unit test
- credential resolution tests
- a fake-stream contract test
- one opt-in live smoke test
- model catalog and thinking-level validation
- cancellation behavior verification

## Provider Credential Redesign

Remove the Pi-specific `pi_credential` field and local Pi credential directories.

Define a runtime-neutral backend credential envelope:

```json
{
  "schema_version": 1,
  "provider": "openai",
  "auth_type": "api_key",
  "secret": {
    "api_key": "..."
  },
  "expires_at": null,
  "metadata": {}
}
```

OAuth example:

```json
{
  "schema_version": 1,
  "provider": "openai-codex",
  "auth_type": "oauth",
  "secret": {
    "access_token": "...",
    "refresh_token": "..."
  },
  "expires_at": "2026-07-23T18:00:00Z",
  "metadata": {
    "account_id": "..."
  }
}
```

Backend changes:

- rename Pi credential serializers and fields to provider-neutral names
- make the credential envelope provider-versioned
- return credentials only to an authenticated runtime identity
- support atomic compare-and-set updates after OAuth refresh
- keep provider sign-in attempt state in the backend
- never require Tau's local `credentials.json` as the source of truth

Astro changes:

- hydrate credentials into memory for the active provider/session
- pass credentials directly into provider configuration or resolvers
- flush refreshed OAuth credentials immediately through the Python backend client
- invalidate credential caches on authentication failures
- redact credential objects through the structured logging sanitizer

Delete:

- `PI_CODING_AGENT_DIR`
- `ASTRO_PROVIDER_CREDENTIAL_DIR`
- Pi auth file hydration
- Pi auth file hashing
- periodic credential file flushes

## Session Storage and Backend Redesign

Pi checkpoint compatibility remains available for Pi-discriminated sessions.
Tau sessions do not create or consume Pi checkpoint bundles.

Implement Tau's `SessionStorage` protocol directly against backend session-entry APIs:

```python
class BackendSessionStorage(SessionStorage):
    async def append(self, entry: SessionEntry) -> None: ...

    async def read_all(self) -> list[SessionEntry]: ...
```

### Backend Data Model

Add or replace backend models with:

```text
AgentSession
  uid
  harness = "pi" | "tau"
  harness_protocol = "pi-checkpoint-v1" | "tau-session-v1"
  harness_version

TauAgentSessionEntry
  agent_session
  sequence
  entry_json
  idempotency_key

AgentSessionLease
  existing fields unchanged
```

Constraints:

- `(agent_session, sequence)` is unique.
- `(agent_session, idempotency_key)` is unique.
- appends require a valid runtime lease token.
- appends use an expected next sequence to reject concurrent writers.
- entry JSON is validated as a native Tau entry shape.
- session history is append-only for the initial implementation.
- compaction entries are stored natively.
- the backend does not parse Pi `firstKeptEntryId` or Pi session headers.

Do not implement compaction pruning during the first cutover. Store all native Tau entries until
resume, branching, compaction, and history projection are stable. Backend retention can be added
later using Tau's `replaces_entry_ids` semantics.

### Backend Session APIs

Implement typed endpoints equivalent to:

```text
POST  /orm/api/agents/v1/sessions/{uid}/checkpoint_lease/acquire/
POST  /orm/api/agents/v1/sessions/{uid}/checkpoint_lease/renew/
POST  /orm/api/agents/v1/sessions/{uid}/checkpoint_lease/release/
GET   /orm/api/agents/v1/sessions/{uid}/entries/
POST  /orm/api/agents/v1/sessions/{uid}/entries/append/
GET   /orm/api/agents/v1/sessions/{uid}/runtime_state/
PATCH /orm/api/agents/v1/sessions/{uid}/runtime_state/
```

The `checkpoint_lease/*` URLs are shared by Pi and Tau. Django dispatches their
implementation from the session harness; Astro must not introduce
`runtime_lease/*` or Tau-specific lease URLs.

Append requests must include:

```json
{
  "lease_token": "...",
  "expected_sequence": 42,
  "idempotency_key": "...",
  "entry": {}
}
```

### Runtime Lease Behavior

- acquire the lease before loading or mutating a durable session
- renew the lease in an AnyIO task group
- stop accepting prompts when renewal fails
- cancel the active Tau turn if ownership is lost
- release the lease after idle eviction or terminal shutdown
- reject a second writer at the backend transaction boundary
- expose lease ownership and expiry in structured diagnostics

### Session Lifecycle

The Python `SessionRuntimeManager` owns:

- a map keyed by backend `AgentSession` UID
- one `asyncio.Lock` per session
- one loaded `CodingSession` per active session
- a provider instance and backend storage instance
- a cancellation scope for the active turn
- runtime lease renewal
- idle TTL and eviction
- shutdown draining

Same-session prompts are serialized. Different sessions may run concurrently.

On cache miss:

1. Acquire the backend runtime lease.
2. Read native Tau entries from the backend.
3. Resolve session configuration, provider credentials, capabilities, skills, and CodeRepository context.
4. Construct and load `CodingSession`.
5. Register the active session runtime.
6. Dispatch the prompt.

On eviction:

1. Refuse new work for the evicting runtime.
2. Wait for or cancel the active turn according to shutdown policy.
3. Close the Tau session and provider HTTP resources.
4. Release the backend runtime lease.
5. Remove the runtime from the registry.

## Main Sequence Python Client

Replace every TypeScript `fetch` client with a typed async Python client based on one shared
`httpx.AsyncClient`.

Create capability-specific client modules for:

- runtime authentication and bootstrap
- runtime identity
- agent sessions
- native Tau session entries
- runtime leases
- session configuration
- provider credentials and sign-in
- model catalog
- capabilities and materialization
- session insights
- A2A task persistence

Client requirements:

- Pydantic request and response models
- explicit connect, read, write, and pool timeouts
- retries only for safe or idempotent operations
- idempotency keys for appends and task mutation
- cancellation propagated from request scope
- normalized backend errors
- response body size limits
- credential and token redaction
- one lifecycle-managed connection pool

The backend base URL in container development points to Django on port `8000`, for example:

```text
MAINSEQUENCE_BACKEND=http://host.docker.internal:8000
```

Astro continues listening on its own HTTP port, currently `8787`. The Django backend and Astro
service must not be configured to listen on the same container port.

## Tau Runtime Integration

Use `tau_coding.CodingSession` for coding-session behavior and `tau_agent.AgentHarness` only where a
lighter stateless runtime is sufficient.

### Durable Chat and A2A

Use `CodingSession` because it provides:

- session entries
- replay
- compaction
- model changes
- thinking levels
- custom entries
- skills and CodeRepository context
- steering and follow-up queues
- cancellation

### Stateless LLM Passthrough

Use a short-lived `AgentHarness` or direct provider stream depending on whether tool execution is
allowed. Do not create a durable backend session for a stateless request.

### Event Translation

Create one internal Astro event model:

```text
Tau event
  -> AstroRuntimeEvent
  -> assistant-ui encoder
  -> A2A encoder
  -> structured logging
```

The translator must cover:

- agent start and end
- turn start and end
- message start, update, and end
- text deltas
- reasoning deltas
- tool-call start and arguments
- tool execution start, update, and end
- usage and finish reason
- provider retry
- compaction
- cancellation
- runtime error

Do not send raw Pydantic/Tau objects directly to clients.

## HTTP and Protocol Port

Implement the ASGI application with FastAPI and Uvicorn.

Port the existing public behavior for:

- `GET /health`
- `POST /api/llm/chat`
- `POST /api/chat`
- model catalog routes
- provider status and sign-in routes
- session model and configuration routes
- session cancellation
- A2A REST routes
- A2A JSON-RPC
- A2A SSE streaming
- task cancellation and subscription
- push notification configuration
- extended agent cards

The implementation may reorganize response models, but public changes must be intentional and
documented because frontend and A2A callers still depend on these interfaces.

### Streaming Requirements

- use ASGI streaming responses, not buffered responses
- cancel the runtime turn when an A2A streaming client disconnects
- define whether regular UI disconnects cancel or detach from the active turn
- send terminal events exactly once
- preserve `[DONE]` behavior where still required by the frontend protocol
- apply backpressure instead of accumulating unbounded event queues
- use bounded per-request memory

### Strict JSON A2A

Port:

- response schema validation
- JSON extraction
- bounded repair attempts
- repair timeout
- terminal validation errors
- output contract metadata

The repair pass should use the configured provider through the same provider factory, not a special
CLI invocation.

### A2A Task Persistence

Replace in-process task maps with backend-owned A2A task state:

- idempotent message submission
- task state transitions
- artifacts and output parts
- cancellation state
- push notification configurations
- terminal errors
- timestamps

An in-memory event channel may accelerate active SSE subscribers, but the backend remains canonical.

## Tools and Extensions

### Tau Built-In Tools

Reuse Tau's Python coding tools directly:

- `read`
- `write`
- `edit`
- `bash`

These are also Pi's default full-access `codingTools` set. Pi core additionally exports `grep`,
`find`, and `ls`, but those tools belong to Pi's optional read-only/all-tools sets rather than its
default coding set.

Tau `v0.3.1` intentionally ships the same default four coding tools. Astro must not duplicate Tau
core tools in its own package.

### Independent File Tools Package

Provide `grep`, `find`, and `ls` now through a standalone `tau-file-tools` Python distribution.
These tools are not implemented under `src/astro` and the package must not import Astro.

The package public API is:

```python
from tau_file_tools import create_find_tool, create_grep_tool, create_ls_tool

tools = [
    create_grep_tool(cwd=code_repository_cwd),
    create_find_tool(cwd=code_repository_cwd),
    create_ls_tool(cwd=code_repository_cwd),
]
```

Each factory returns a Tau `AgentTool` and depends only on public Tau contracts.

Package requirements:

- preserve Pi's tool names and input schemas
- preserve Pi's result truncation and continuation behavior
- accept an explicit `cwd`
- resolve relative paths against that `cwd`
- support cancellation
- avoid shell interpolation of user arguments
- prefer `rg` when available and provide a deterministic fallback
- enforce output byte and line limits
- keep each tool implementation and test suite independent
- expose no Astro settings, backend models, runtime identity, or deployment behavior
- use the same Python 3.13+ requirement and Tau version constraint as Astro

Astro imports these factories while constructing the `CodingSession` tool list. No Astro adapter is
needed beyond runtime composition.

The package layout and API should follow Tau's existing coding-tool conventions so the three modules
can be submitted upstream with minimal editing. After Tau publishes equivalent
`create_grep_tool`, `create_find_tool`, and `create_ls_tool` factories:

1. Change Astro imports from `tau_file_tools` to `tau_coding`.
2. Remove the `tau-file-tools` dependency.
3. Remove the standalone package only after the minimum supported Tau version includes all three
   tools.

### Astro Tools

Implement only Astro-specific tools:

- `get_runtime_info`
- Main Sequence capability tools
- any project-specific tool currently exposed through Pi overlays

`get_runtime_info` remains in Astro because it reports Astro release, Python runtime, execution
mode, code repository image, workspace, and deployment paths. It is not a generic coding-agent primitive and
does not belong in Tau core.

Each tool must define:

- stable JSON schema
- async execution
- cancellation
- output byte limits
- timeout
- cwd rules
- path traversal rules
- structured error output
- tests for malformed input

### Web Access Package Integration

Do not redesign web access as an Astro subsystem.

The current `pi-web-access` npm package is not directly importable from Python:

- its package manifest exposes a Pi extension entrypoint, not a CLI
- it has no `bin` executable
- it has no Python distribution
- its default export receives Pi's `ExtensionAPI`
- its tool registration depends on Pi, `pi-ai`, `pi-tui`, and TypeBox
- its curator HTTP server is a browser UI helper, not a tool invocation API
- Tau does not currently ship an equivalent web-search or content-fetch package

The underlying implementation contains reusable TypeScript functions, but importing those functions
would still require a JavaScript runtime. That conflicts with the fixed requirement to remove Node.

The correct monorepo package boundary is:

```text
packages/tau-web-access
  -> standalone Python package
  -> Tau AgentTool registrations
  -> source-preserving port of pi-web-access behavior

astro
  -> imports tau_web_access
  -> supplies configuration and credentials
  -> registers the returned AgentTool objects with CodingSession
```

`tau-web-access` must remain a separately buildable Python distribution within the Astro monorepo.
It must not be created or maintained as a sibling repository. If the upstream maintainer publishes
an official Tau/Python package before this migration reaches implementation, use that package
instead of maintaining the workspace package.

Astro contains only a thin adapter:

```python
from tau_web_access import create_web_tools


def build_web_tools(settings, credentials):
    return create_web_tools(settings=settings, credentials=credentials)
```

The standalone port must preserve the existing agent-facing contract rather than inventing new
tools:

- `web_search`
- `code_search`
- `fetch_content`
- `get_search_content`

Preserve:

- tool names
- input schemas and defaults
- result and error shapes
- response IDs and stored-content retrieval
- search-provider fallback behavior
- GitHub cloning behavior
- readable HTML extraction
- PDF extraction
- YouTube and video handling
- content limits
- cancellation
- SSRF protection

The interactive Pi commands and TUI rendering are not part of Astro's headless HTTP tool contract.
They may remain optional features of the standalone package, but Astro imports only the four
`AgentTool` definitions.

The port may replace language-specific implementation dependencies with Python equivalents, but that
is an implementation port, not a product or tool-contract redesign.

Do not preserve Node merely for this feature. Publication of `tau-web-access` with contract tests is
a prerequisite for deleting `pi-web-access`.

### Skills and CodeRepository Context

Use Tau's native support for:

- `AGENTS.md`
- `.agents` resources
- skills
- prompt templates
- system prompt composition

Port Main Sequence behavior into Python:

- resolve Main Sequence skill paths through the Python SDK
- materialize backend session capabilities
- inject runtime identity and project policy
- expose runtime information
- emit telemetry

Do not copy JavaScript Pi extensions into the new runtime.

## Single Python Environment Contract

The current remote-worker image overlays a Node Astro runtime onto an arbitrary code repository image. That
split is removed.

Every project base image must satisfy:

```text
python --version >= 3.13
python can install/import the Astro wheel
python can import project dependencies
python is the interpreter used by Astro and Tau tools
```

### CodeRepository Image Policy

- code repository image templates are upgraded to Python 3.13 or newer
- image publication fails if `sys.version_info < (3, 13)`
- Astro is installed into the code repository image's active Python environment
- Tau and Main Sequence dependencies resolve in that same environment
- dependency conflicts fail the code repository image build
- no second virtual environment is created to hide dependency conflicts
- no `/opt/astro` interpreter is installed
- `bash` and Python tool subprocesses inherit the same environment

If a project uses a virtual environment, that environment is the single image environment and must
be active through `PATH` and `VIRTUAL_ENV` before Astro starts. Astro does not create another one.

### Runtime Verification

The container integration suite must prove:

```python
service_sys_executable == tool_subprocess_sys_executable
service_python_version >= (3, 13)
project_dependency_import_succeeds
tau_import_succeeds
mainsequence_import_succeeds
```

## Docker and Deployment

### Main Dockerfile

Replace the current multi-runtime build with:

```text
python:3.13-slim
  -> install system dependencies
  -> install locked Python dependencies
  -> install Astro
  -> install Playwright Chromium if web tools require it
  -> run tests/build checks
  -> run uvicorn
```

Remove:

- NodeSource setup
- `nodejs`
- `npm install`
- `package.json` version discovery
- Pi RPC patching
- Pi overlay copies
- sidecar image targets
- Pi image aliases

Use one production command:

```text
python -m uvicorn astro.app:app --host 0.0.0.0 --port 8787
```

### Remote Worker Dockerfile

The new remote worker build:

1. Starts from a project `BASE_IMAGE` that already satisfies Python 3.13+.
2. Copies an Astro wheel and lock/exported constraint artifact from the Astro build stage.
3. Installs Astro into the active project Python environment.
4. Validates Python, Tau, Main Sequence, and project imports.
5. Starts Uvicorn from the code repository cwd.

Do not install Node.

### Docker Compose

Replace the two-service Astro topology with one service:

```text
astro-runtime
```

Remove:

- `astro-pi-stream`
- `astro-session-checkpoint-sidecar`
- the shared session-state volume used for Pi JSONL
- Pi directories and Pi package mounts
- sidecar timing environment variables

Configure the Astro container to call Django on port `8000`.

### Kubernetes

Replace the two-container pod with one Astro container.

Remove:

- checkpoint sidecar container
- `/session-state` shared volume unless another non-session temporary use remains
- Pi-specific environment variables
- sidecar `preStop`

Add:

- startup probe
- readiness probe
- liveness probe
- graceful ASGI shutdown
- runtime lease release during shutdown
- a termination grace period that covers active-turn draining

## Environment Variable Cleanup

Replace Pi and process-runner variables instead of supporting aliases.

Delete:

- `PI_CODING_AGENT_DIR`
- `ASTRO_PI_PACKAGE_PATHS`
- `ASTRO_RUNTIME_PROJECT_PI_DIR`
- `ASTRO_ORCHESTRATOR_PROJECT_PI_DIR`
- `ASTRO_PROVIDER_CREDENTIAL_DIR`
- `ASTRO_PROVIDER_CREDENTIAL_FLUSH_INTERVAL_MS`
- `ASTRO_A2A_WARM_RUNNERS`
- `ASTRO_A2A_WARM_RUNNER_IDLE_TTL_MS`
- `ASTRO_A2A_WARM_RUNNER_STARTUP_TIMEOUT_MS`
- `ASTRO_A2A_WARM_RUNNER_RPC_TIMEOUT_MS`
- `ASTRO_A2A_WARM_RUNNER_TURN_TIMEOUT_MS`
- every `ASTRO_CHECKPOINT_SIDECAR_*` variable

Define a smaller Pydantic settings contract:

- `ASTRO_HOST`
- `ASTRO_PORT`
- `ASTRO_CODE_REPOSITORY_CWD`
- `ASTRO_HOME`
- `ASTRO_SESSION_IDLE_TTL_SECONDS`
- `ASTRO_SESSION_LEASE_TTL_SECONDS`
- `ASTRO_SESSION_LEASE_RENEW_SECONDS`
- `ASTRO_TURN_TIMEOUT_SECONDS`
- `ASTRO_SHUTDOWN_GRACE_SECONDS`
- `ASTRO_LOG_LEVEL`
- `ASTRO_LOG_MACHINE_SINK`
- `ASTRO_LOG_HUMAN_SINK`
- `ASTRO_LOG_PAYLOADS`
- `MAINSEQUENCE_BACKEND`
- runtime authentication values required by the backend

Provider API keys remain supported only where the deployment deliberately supplies them. Backend
credentials are preferred for managed sessions.

## Observability

Port structured logging before route cutover.

Replace Pi-specific events with runtime-neutral events:

```text
runtime.session.load.started
runtime.session.load.completed
runtime.turn.started
runtime.turn.completed
runtime.turn.cancelled
runtime.provider.request.started
runtime.provider.request.retry
runtime.provider.request.completed
runtime.tool.started
runtime.tool.completed
runtime.lease.acquired
runtime.lease.renewed
runtime.lease.lost
runtime.session.evicted
```

Every request and turn log should include, when available:

- request ID
- user UID
- agent session UID
- A2A task ID
- provider
- model
- runtime version
- Tau version
- duration
- terminal status

Do not log prompts, tool output, credentials, OAuth tokens, or provider headers unless an explicit
development-only payload setting allows sanitized previews.

## Testing Strategy

### Contract Fixtures

Retain and expand JSON fixtures for:

- assistant-ui streaming
- A2A message send
- A2A JSON-RPC
- task responses
- cancellation
- strict JSON output
- model catalog
- provider auth
- session configuration

The old TypeScript implementation may be used to generate expected public fixtures before it is
deleted. The new implementation does not need to preserve Pi-internal event names.

### Unit Tests

Cover:

- Tau-to-Astro event translation
- assistant-ui encoding
- A2A encoding and routing
- strict JSON validation and repair
- provider factory selection
- every custom provider adapter
- credential redaction and refresh
- backend transport models
- session registry locking
- cancellation
- lease renewal and lease loss
- tool schemas and limits
- history projection

### Integration Tests

Run with:

- fake Tau provider streams
- a disposable Django test backend
- two Astro instances competing for one session lease
- real backend session entry append/read
- client disconnect during streaming
- shutdown during an active turn
- session eviction and reload
- provider credential refresh
- A2A task persistence

### Container Tests

Assert:

- Python is 3.13 or newer
- `node` is not installed
- npm files are absent
- Tau imports
- Astro starts
- health/readiness passes
- Django on port `8000` is reachable from Astro
- the service and tool subprocess use the same Python executable
- project dependencies are importable from a Tau tool execution
- Playwright works if browser tools are enabled

### Live Provider Tests

Keep live tests opt-in and secret-gated. At minimum validate:

- OpenAI API key
- Anthropic API key
- OpenAI Codex OAuth
- Google Gemini
- one OpenAI-compatible third-party provider
- every required Astro custom provider

## Implementation Phases

### Phase 0: Freeze Scope and Inventory

- [ ] Export the backend's actual enabled provider and model catalog.
- [ ] Classify every provider with the provider acceptance matrix.
- [ ] Inventory every existing public route and response shape.
- [x] Inventory all `pi-web-access` behavior currently required in production.
- [ ] Capture public protocol fixtures from the current service.
- [x] Decide whether existing backend sessions are deleted or reset in place.
- [ ] Define the coordinated backend and Astro cutover window.

Exit criteria:

- every currently supported provider has a declared Tau or Astro implementation path
- every public route has an owner and test fixture
- no Pi data migration is in scope

### Phase 1: Create the Python Application

- [x] Add `pyproject.toml`, `uv.lock`, and `.python-version`.
- [x] Add the `src/astro` package.
- [x] Add Pydantic settings.
- [x] Add structured logging.
- [x] Add FastAPI application lifecycle.
- [x] Add health, readiness, and version endpoints.
- [x] Add pytest, ruff, and mypy checks.
- [x] Pin `tau-ai==0.3.1`.

Exit criteria:

- the Python service starts on port `8787`
- tests and static checks pass without invoking Node

### Phase 2: Build the Python Backend Client

- [x] Port runtime authentication.
- [x] Port identity and bootstrap.
- [x] Port session configuration.
- [x] Port model catalog.
- [x] Port provider credential status and sign-in.
- [x] Port capability materialization.
- [x] Add native Tau entry and existing checkpoint lease client methods.
- [x] Add exact Django-shape client fixture tests.
- [ ] Add a live Django integration contract test.

Exit criteria:

- no new Python runtime path calls the TypeScript backend adapter
- all backend payloads are represented by typed Python models

### Phase 3: Change the Backend Contracts

- [ ] Add native Tau session entry storage.
- [ ] Dispatch the existing checkpoint lease APIs by harness.
- [ ] Preserve Pi checkpoint APIs while adding Tau persistence.
- [x] Replace `pi_credential` with provider-neutral credential envelopes.
- [x] Add compare-and-set OAuth refresh.
- [x] Persist A2A tasks and push configurations.
- [ ] Remove Pi compaction normalization and retention code.
- [ ] Add non-destructive Pi/Tau harness migrations.

Exit criteria:

- a Python test can acquire a lease, append Tau entries, restart, and reload the session
- the backend supports both Pi checkpoint persistence and Tau entry persistence
  behind one harness-aware session API

### Phase 4: Implement Provider Support

- [x] Implement `AstroProviderFactory`.
- [x] Map backend definitions to Tau built-in protocols.
- [x] Implement backend-backed credential resolution.
- [x] Implement custom-provider registration through Tau's public protocol.
- [x] Add model and thinking-level validation.
- [x] Add provider cancellation and close behavior.
- [x] Run fake-stream tests for every provider class.

Exit criteria:

- every enabled backend provider resolves to a tested `ModelProvider`
- unsupported providers are absent from the backend catalog

### Phase 5: Implement Tau Session Runtime

- [x] Implement `BackendSessionStorage`.
- [x] Implement `SessionRuntimeManager`.
- [x] Load and resume native Tau sessions.
- [x] Implement same-session serialization.
- [x] Implement different-session concurrency.
- [x] Implement runtime lease renewal.
- [x] Implement idle eviction.
- [x] Implement cancellation and shutdown draining.
- [x] Implement Tau-to-Astro runtime events.

Exit criteria:

- durable turns survive service restart
- concurrent writers are rejected
- cancellation terminates provider and tool work
- no Pi process is launched

### Phase 6: Port HTTP, Streaming, and A2A

- [x] Port chat routes.
- [x] Port stateless LLM route.
- [x] Port model and provider routes.
- [x] Port session configuration and cancellation.
- [x] Port A2A REST.
- [x] Port A2A JSON-RPC.
- [x] Port SSE task streaming.
- [x] Port strict JSON validation and repair.
- [x] Port file and PDF A2A parts.
- [x] Port task subscription and push configuration.

Exit criteria:

- public contract fixtures pass
- disconnect, timeout, cancellation, and terminal events are deterministic

### Phase 7: Port Tools, Skills, and Web Access

- [x] Register Tau built-in coding tools.
- [x] Create the independently packageable `packages/tau-file-tools` workspace distribution.
- [x] Implement independent Pi-compatible `grep`, `find`, and `ls` tool factories.
- [ ] Publish and pin the `tau-file-tools` wheel.
- [x] Import the three factories when Astro constructs a `CodingSession`.
- [x] Port runtime-info.
- [x] Port capability materialization.
- [x] Port skill discovery and CodeRepository context.
- [x] Port project policy and telemetry.
- [x] Port the `packages/tau-web-access` workspace distribution without changing its tool contract.
- [ ] Publish and pin the `tau-web-access` wheel if it is consumed outside the monorepo.
- [x] Add the thin Astro `web_access.py` registration adapter.
- [x] Run tool security and output-limit tests.

Exit criteria:

- Tau supplies the default `read`, `write`, `edit`, and `bash` coding tools
- `tau-file-tools` supplies tested `grep`, `find`, and `ls` tools without importing Astro
- Astro supplies only Astro-specific runtime and capability tools
- `tau-web-access` passes compatibility tests for the four existing web tools
- no Node dependency remains for web access

### Phase 8: Replace Images and Deployment

- [x] Enforce Python 3.13+ in project base image installation.
- [x] Install Astro into the project's single Python environment.
- [x] Replace the main Dockerfile.
- [x] Replace the remote-worker Dockerfile.
- [x] Reduce Docker Compose to one Astro service.
- [x] Reduce Kubernetes to one Astro container.
- [x] Add container single-environment assertions.

Exit criteria:

- production-equivalent images contain no Node
- project imports and Tau tools use the same interpreter
- the sidecar does not exist

### Phase 9: Delete Pi and TypeScript

- [x] Delete `package.json`.
- [x] Delete `package-lock.json`.
- [x] Delete `tsconfig.json`.
- [x] Delete `.pi`.
- [x] Delete `pi/`.
- [x] Delete Pi overlay packages.
- [x] Delete TypeScript route and adapter implementations.
- [x] Delete Pi history projector.
- [x] Delete Pi RPC patch.
- [x] Delete warm-runner configuration.
- [x] Delete checkpoint sidecar.
- [x] Delete JavaScript A2A tools after Python replacements exist.
- [x] Remove npm and Node instructions from active documentation.
- [x] Rename active images, services, and deployment resources that contain `pi`.

Exit criteria:

```text
command -v node -> not found
find . -name '*.ts' -> no production TypeScript
find . -name '*.mjs' -> no production JavaScript
test ! -f package.json
test ! -d .pi
test ! -d pi
```

References to Pi may remain only in historical migration documentation and upstream Tau attribution.

### Phase 10: Coordinated Cutover

- [ ] Stop creation of new Pi-backed sessions.
- [ ] Drain active Pi requests.
- [ ] Take a backend database snapshot.
- [ ] Apply the destructive backend session/checkpoint migration.
- [ ] Deploy the Python Astro image.
- [ ] Run provider, session, A2A, and web-tool smoke tests.
- [ ] Reopen traffic.
- [ ] Monitor lease loss, provider failures, stream completion, and task terminal rates.

Rollback does not use schema compatibility. If rollback is required after destructive migration,
restore the database snapshot and the previous backend and Astro images together.

## Deletion Map

| Current path or component | Final action |
| --- | --- |
| `interface/stream/server.ts` | Replace with FastAPI routes and runtime services |
| `interface/stream/*.ts` | Port required contracts, then delete |
| `adapters/*.ts` | Replace with Python backend protocol and client |
| `adapters/mainsequence/*.ts` | Replace with Python Main Sequence adapter |
| `adapters/mainsequence/pi-overlay` | Port required behavior, then delete |
| `pi/` | Port tools/extensions, then delete |
| `.pi/` | Delete |
| `runtime/checkpoints/sidecar.ts` | Replace with backend session storage, then delete |
| `runtime/bootstrap/pi-agent-dir.mjs` | Delete |
| `tools/build/patch-pi-rpc-ready.mjs` | Delete |
| `tools/a2a/*.mjs` | Replace with Python operational clients |
| `tests/*.test.ts` | Port assertions to pytest, then delete |
| `package.json` and lockfile | Delete |
| `tsconfig.json` | Delete |
| sidecar container | Delete |
| Node image installation | Delete |
| `pi-web-access` dependency | Replace with the standalone `tau-web-access` dependency |

## Risks and Controls

| Risk | Control |
| --- | --- |
| Tau API changes rapidly | Exact pin, lockfile, runtime contract tests |
| One environment exposes dependency conflicts | Resolve at code-repository-image build time and fail explicitly |
| In-process extensions can crash the service | Load only trusted Astro extensions; isolate untrusted project commands through tools/container boundaries |
| Backend entry writes increase request volume | Connection pooling, idempotent append API, optional bounded batching after correctness |
| Provider behavior differs from Pi | Provider-by-provider contract and live smoke tests |
| OAuth refresh loses updates | Backend compare-and-set versioning |
| Session lease loss during a turn | Immediate cancellation and append rejection |
| Web-access feature loss | Explicit subfeature inventory and acceptance matrix |
| No schema-compatible rollback | Coordinated deployment plus database snapshot |
| ASGI disconnect semantics differ from Node | Dedicated disconnect and terminal-event integration tests |

## Definition of Done

The migration is complete only when:

- Astro runs entirely on Python 3.13 or newer.
- Astro and CodeRepository code use the same Python environment.
- Tau runs in-process as a library.
- Every enabled provider has a working Tau or Astro provider implementation.
- Custom providers are supported through Tau's `ModelProvider` protocol.
- Durable sessions use native Tau entries stored by the backend.
- Runtime leases prevent concurrent session writers.
- Provider credentials are runtime-neutral and backend-owned.
- The Python backend client covers every runtime operation.
- HTTP, assistant-ui, A2A REST, A2A JSON-RPC, and SSE contract tests pass.
- Strict JSON repair, files, PDFs, cancellation, and task persistence pass.
- All required coding tools have Python implementations.
- `tau-file-tools` provides Pi-compatible `grep`, `find`, and `ls` tools as an external dependency.
- `tau-web-access` passes compatibility tests for the four existing web tools.
- Docker Compose and Kubernetes run one Astro service container.
- CodeRepository images enforce Python 3.13+.
- Node is absent from production images.
- Pi, Pi overlays, Pi JSONL, Pi RPC, npm files, and the checkpoint sidecar are deleted.
- Documentation describes only the Python/Tau architecture.

## Tau Source References

- [Tau repository](https://github.com/huggingface/tau)
- [Tau v0.3.1 source](https://github.com/huggingface/tau/tree/v0.3.1)
- [Tau provider protocol](https://github.com/huggingface/tau/blob/v0.3.1/src/tau_agent/provider.py)
- [Tau provider runtime](https://github.com/huggingface/tau/blob/v0.3.1/src/tau_coding/provider_runtime.py)
- [Tau built-in provider catalog](https://github.com/huggingface/tau/blob/v0.3.1/src/tau_coding/data/catalog.toml)
- [Tau provider guide](https://twotimespi.dev/guides/providers-and-models/)
- [Tau architecture](https://twotimespi.dev/internals/architecture/)
- [Tau custom frontend guide](https://twotimespi.dev/internals/custom-frontend/)
- [Pi core tool factories](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/index.ts)
- [Pi Web Access repository](https://github.com/nicobailon/pi-web-access)
- [Pi Web Access package manifest](https://github.com/nicobailon/pi-web-access/blob/main/package.json)
