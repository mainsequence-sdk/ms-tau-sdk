# Repository Structure

```text
├── src/ms_tau_sdk/
│   ├── agents/       # sessionless harness construction
│   ├── agent_skills/ # version-matched development instructions
│   ├── api/          # FastAPI transport routers
│   ├── backend/      # authenticated API and MCP clients
│   ├── protocols/    # A2A, assistant-stream, and strict-JSON codecs
│   ├── providers/    # provider evidence validation and construction
│   ├── resources/    # packaged Tau defaults
│   ├── runtime/      # durable Tau lifecycle and event flow
│   ├── sessions/     # durable storage adapter
│   ├── tools/        # MCP projection and task controls
│   ├── app.py
│   ├── cli.py
│   ├── skills.py
│   └── settings.py
├── tests/
│   ├── contract/
│   ├── fixtures/sdk-consumer-project/
│   └── unit/
├── docs/
├── scripts/            # distribution and clean-install release gates
├── .github/workflows/  # quality and protected Python release automation
├── pyproject.toml
└── uv.lock
```

The repository has no container, Compose, Kubernetes, image-build, overlay, or runtime-wheelhouse
tree. The wheel contains only the `ms_tau_sdk` package, metadata, required Tau resources, and the
version-matched development skill bundle. The consumer fixture is a separate locked Python project
and is not part of the wheel. Skills are copied only by an explicit `ms-tau skills sync` request.
