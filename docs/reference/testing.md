# Testing and Release Gates

Run the local quality suite with:

```bash
uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest
```

The suites are divided by responsibility:

- contract tests freeze HTTP operations, backend requests, auth behavior, persistence, task replay,
  and project consumption;
- unit tests cover transports, event encoding, providers, settings, Tau resources/extensions,
  explicit managed-skill synchronization, durable runtime behavior, storage, observability,
  cancellation, and shutdown; and
- the credential-gated end-to-end test exercises a real existing session when explicitly enabled.

Distribution gates build both artifacts, inspect their names and contents, install the wheel into a
clean environment, construct the public API without source-path leakage, run `ms-tau` from a project
workspace, synchronize the SDK skill namespace, call health/version, and verify graceful shutdown.

The quality workflow runs those gates for every pull request and development/main push. The
separate [Python release process](./releasing.md) emits dependency metadata, checksums, provenance,
and an attested candidate bundle; only an exact version tag can enter the protected PyPI publication
job.

The project fixture has its own `pyproject.toml` and `uv.lock`. CI synchronizes it separately and
imports its three-line ASGI shim to prevent accidental reliance on repository-only integration code.
