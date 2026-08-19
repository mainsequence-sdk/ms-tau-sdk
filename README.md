# Astro

Astro is Main Sequence's Python 3.13 agent service built on
[Hugging Face Tau](https://github.com/huggingface/tau). It exposes durable
Assistant UI chat, stateless model chat, and standard A2A transports.

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
project runtime credentials are not valid for MCP.
Provider API keys remain backend-owned and are hydrated per user/session.

For a Project Executor deployment, Django injects
`MAIN_SEQUENCE_ORGANIZATION_PROJECT_ENVIRONMENT_UID` from the service's
persisted ProjectBranch. Astro uses it automatically for `agent.list` and
`agent.search`, overwrites any caller-provided value, and hides the selector
from Tau. One Astro Project Executor deployment serves exactly that one
environment; users and project code do not select or switch it.

See [`.env.example`](./.env.example) for optional web-provider settings.

## Container Startup

The Compose build context is the Astro repository. The independently
packageable `tau-file-tools` and `tau-web-access` distributions are workspace
members under `packages/`.

`uv.lock` is the source dependency lock. `requirements-runtime.lock` is its
hash-locked export used to build an offline wheelhouse for the runtime and
project-executor images.

```bash
docker compose up --build astro
```

Compose always runs Astro in the container and points it at Django on host port
`8000` by default:

```text
http://api.main-sequence.app:8000
```

Override `ASTRO_PROJECT_PATH` when the mounted project is not the Astro checkout.

Verify a built image's Python environment, runtime tools, Node absence, and
health endpoints with:

```bash
./scripts/verify-runtime-image.sh astro:tau
```

## Public APIs

- `GET /health`
- `GET /ready`
- `GET /version`
- `POST /api/chat`
- `POST /api/llm/chat`
- `GET /api/models/catalog`
- `GET /api/model-providers`
- `POST /api/model-providers/{provider}/signin`
- `POST /api/model-providers/{provider}/signoff`
- `POST /api/a2a/v1/message:send`
- `POST /api/a2a/v1/message:stream`
- `GET /api/a2a/v1/tasks`
- `POST /api/a2a/rpc`

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
