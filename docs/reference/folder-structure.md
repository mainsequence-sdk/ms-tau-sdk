# Folder Structure

```text
astro/
├── src/astro/
│   ├── api/
│   │   ├── a2a.py
│   │   ├── chat.py
│   │   ├── health.py
│   │   ├── llm.py
│   │   ├── providers.py
│   │   └── sessions.py
│   ├── backend/
│   │   ├── auth.py
│   │   ├── client.py
│   │   ├── mcp.py
│   │   └── models.py
│   ├── protocols/
│   │   └── assistant_ui.py
│   ├── providers/
│   │   ├── catalog.py
│   │   ├── definitions.py
│   │   └── factory.py
│   ├── resources/
│   │   ├── APPEND_SYSTEM.md
│   │   ├── CHILD_POLICY.md
│   │   ├── loader.py
│   │   └── prompts/
│   ├── runtime/
│   │   ├── events.py
│   │   ├── manager.py
│   │   └── session.py
│   ├── sessions/
│   │   └── storage.py
│   ├── tools/
│   │   ├── mainsequence_mcp.py
│   │   ├── runtime_info.py
│   │   └── web_access.py
│   ├── app.py
│   └── settings.py
├── packages/
│   ├── tau-file-tools/
│   │   ├── src/tau_file_tools/
│   │   │   ├── grep.py
│   │   │   ├── find.py
│   │   │   └── ls.py
│   │   ├── tests/
│   │   └── pyproject.toml
│   └── tau-web-access/
│       ├── src/tau_web_access/
│       │   ├── extractors.py
│       │   ├── providers.py
│       │   ├── security.py
│       │   ├── storage.py
│       │   └── tools.py
│       ├── tests/
│       └── pyproject.toml
├── tests/
├── deployment/
├── Dockerfile
├── Dockerfile.remote-worker
├── docker-compose.yml
└── pyproject.toml
```

`tau-file-tools` and `tau-web-access` are independent Python distributions
inside the Astro monorepo, not modules under the `astro` distribution. The root
`uv` workspace resolves them as normal Python dependencies, and the container
build installs all three distributions into the same Python 3.13 environment.

Tau session history is backend-owned. There is no local session-state folder,
Pi JSONL, shared checkpoint volume, or sidecar.
