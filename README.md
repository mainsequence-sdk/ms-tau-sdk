# Astro

Astro is Main Sequence's Python 3.13 agent service built on
[Hugging Face Tau](https://github.com/huggingface/tau). It exposes durable
Assistant UI chat, agent-targeted sessionless responses, and standard A2A transports.

Astro is container-only. Do not run a second host Python or Node runtime.

## Runtime

- Python `>=3.13`
- Tau `0.3.1`
- FastAPI/Uvicorn on port `8787`
- Django backend on `MAINSEQUENCE_BACKEND`
- Main Sequence MCP automatically loaded from `{MAINSEQUENCE_BACKEND}/mcp`
- Native Tau session entries persisted by Django
- One process and one Python environment
- Hash-locked runtime wheelhouse generated from `uv.lock`
- No Node.js, Pi runtime, JSONL checkpoint, or checkpoint sidecar

## Required Environment

```dotenv
MAINSEQUENCE_BACKEND=http://api.main-sequence.app:8000
MAINSEQUENCE_AUTH_MODE=runtime_credential
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID=replace-with-coding-agent-service-credential-id
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET=replace-with-coding-agent-service-credential-secret
```

The runtime credential pair is required because Astro authenticates every
session, task, provider credential, lease request, and MCP call to Django.
It must belong to the deployed coding-agent service; organization-test and
code repository runtime credentials are not valid for MCP.
Provider API keys remain backend-owned and are hydrated for the exact session
or Agent execution identity.

For a CodeRepository Executor deployment, Django derives Agent discovery scope from
the authenticated service credential and the service's persisted
CodeRepositoryBranch. Astro sends no Environment selector and hides that selector from
Tau. One Astro CodeRepository Executor deployment serves exactly that one Environment;
users and code repository code do not select or switch it.

See [`.env.example`](./.env.example) for optional web-provider settings.

## Container Startup

The Compose build context is the Astro repository. The independently
packageable `tau-file-tools` and `tau-web-access` distributions are workspace
members under `packages/`.

`uv.lock` is the source dependency lock. `requirements-runtime.lock` is its
hash-locked export used to build an offline wheelhouse for the runtime and
code-repository-executor images.

```bash
docker compose up --build astro
```

Compose always runs Astro in the container and points it at Django on host port
`8000` by default:

```text
http://api.main-sequence.app:8000
```

Override `ASTRO_CODE_REPOSITORY_PATH` when the mounted code repository is not the Astro checkout.

Verify a built image's Python environment, runtime tools, Node absence, and
health endpoints with:

```bash
./scripts/verify-runtime-image.sh astro:tau
```

## Real Conversation Verification

The opt-in test builds and starts Astro through local Compose, proves the HTTP
response came from that container, executes two real provider turns, verifies
SSE ordering and UX latency budgets, restarts the container, and proves that
the same conversation resumes from Django-persisted Tau entries:

```bash
ASTRO_REAL_CONVERSATION_SESSION_UID=<existing-tau-session-uid> \
  uv run pytest tests/e2e/test_real_conversation.py -q -s
```

The test requires a backend-owned Tau AgentSession that belongs to the runtime
credential configured in `.env`. It records container startup, response
headers, first SSE event, time to first text, maximum SSE event gap, output
streaming, finish, durability, total-turn, restart, and resumed-turn timings in
`.astro/test-results/real-conversation-timing.json`.

The default UX gates are 0.25 seconds for response headers, 0.5 seconds for the
truthful lifecycle event, 15 seconds for first text, 10 seconds for the maximum
SSE gap, 45 seconds for a complete turn, 2 seconds for durability after
`finish`, and 10 seconds for restart-to-ready. The 2-second durability ceiling
is for the main-orchestrator development profile, where local Django reaches
the development database through a host proxy; ADR 49 retains a 1-second
co-located target. Override gates with the corresponding
`ASTRO_REAL_CONVERSATION_MAX_*_SECONDS` variables or change the report path
with `ASTRO_REAL_CONVERSATION_REPORT_PATH`.

## Public APIs

- `GET /health`
- `GET /ready`
- `GET /version`
- `POST /api/chat`
- `POST /api/agents/{agent_uid}/responses`
- `POST /api/agents/{agent_uid}/responses/stream`
- `GET /api/models/catalog`
- `GET /api/model-providers`
- `POST /api/model-providers/{provider}/signin`
- `GET /api/model-providers/{provider}/signin/{attempt_id}`
- `POST /api/model-providers/{provider}/signin/{attempt_id}/manual`
- `POST /api/model-providers/{provider}/signin/{attempt_id}/cancel`
- `POST /api/model-providers/{provider}/signoff`
- `POST /api/a2a/v1/message:send`
- `POST /api/a2a/v1/message:stream`
- `GET /api/a2a/v1/tasks`
- `POST /api/a2a/rpc`

The unscoped LLM chat surface is not exposed. Agent identity is mandatory for
all one-shot model execution.

FastAPI publishes the full schema at `/docs` and `/openapi.json`.

## Monorepo Packages

```text
astro/
├── packages/
│   ├── tau-file-tools/
│   └── tau-web-access/
├── src/astro/
└── pyproject.toml
```

The tool packages use only Tau's public tool contracts. They remain separately
buildable and can later be released or upstreamed without being separate
repositories today.
