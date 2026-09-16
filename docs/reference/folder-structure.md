# Folder Structure

```text
main-sequence-tau-sdk/
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
│   │   ├── extensions.py
│   │   ├── manager.py
│   │   └── session.py
│   ├── sessions/
│   │   └── storage.py
│   ├── tools/
│   │   ├── mainsequence_mcp.py
│   │   └── task_control.py
│   ├── app.py
│   └── settings.py
├── tests/
├── docs/
└── pyproject.toml
```

The source tree remains `src/astro` only during the bounded extraction phases. Phase C3 replaces it
with `src/ms_tau_sdk`. This repository contains no Docker, Compose, Kubernetes, or image-publication
assets.

The SDK ships no optional model-tool distributions. Tau provides the core coding tools, the SDK
projects backend MCP and A2A protocol tools, and CodeRepositories own optional tools through
`.tau/extensions` and their project dependencies.

Tau session history is backend-owned. There is no local session-state folder,
Pi JSONL, shared checkpoint volume, or sidecar.
