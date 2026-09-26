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
  cancellation, bounded Task waiting, settlement failure, local stale-attempt reconciliation,
  ambiguous side-effect protection, and shutdown; and
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

A Task lifecycle change must inject failure before claim, after claim, during provider/tool output,
during artifact persistence, and during settlement. Local restart tests must prove that submitted
work is safely scheduled, a live lease is respected, stale uncertain work is not replayed, and
every exhausted recovery becomes terminal. Managed terminalization requires deployed-backend
integration evidence; an SDK mock proves request shape but not backend recovery.

Task-history and execution-correlation changes additionally gate on omitted/zero/positive/invalid
`historyLength`, bounded-tail ordering, continuation and responder Messages, A2A v1 role mapping,
URI-array extensions, legacy settlement rejection, identical settlement replay, changed-replay
conflict, and status events that reference rather than embed Messages. Query tests must prove zero
does not read a history tail and Task lists use one bounded history query instead of one per Task.

Local persistence tests must prove adjacent attempts cannot share entries, every Task entry carries
the reserved turn UID, boundaries are exclusive, abandonment rejects late append/commit, and a
Task-owned unresolved turn blocks replacement/release without changing ordinary-turn lease
behavior. Board tests must reconstruct streamed Artifact text without separators, keep revision
events in Technical, show exact attempt entries, and redact/bound the execution projection.
