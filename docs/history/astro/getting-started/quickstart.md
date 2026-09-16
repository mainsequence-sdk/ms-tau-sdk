# Source Quickstart During the SDK Migration

This repository is becoming the `ms-tau-sdk` Python library. It does not own a Docker image,
Compose environment, Kubernetes manifest, or project deployment recipe.

Until Phase C3 changes the package and command names, install and test the current source checkout:

```bash
uv sync --frozen
uv run pytest
```

To exercise the current application locally, provide the Main Sequence backend and scoped runtime
credential variables, then run the temporary compatibility command from the repository root:

```bash
uv run astro-stream
```

Phase C3 replaces that command with the project-installed SDK entrypoint:

```bash
uv run ms-tau
```

Check the service:

```bash
curl http://localhost:8787/health
```

A consuming project owns its dependency lock, `.tau` configuration, operating-system dependencies,
and deployable image. The SDK supplies the application and Tau/Main Sequence integration behavior.
