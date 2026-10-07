# Compatibility Policy

Version `1.0.0` establishes the first stable Main Sequence TAU SDK contract. The project follows
semantic versioning for these supported surfaces:

- the exported Python API: `ms_tau_sdk.create_app`, `TauSDKSettings`, `__version__`,
  `current_requester`, `requester_client`, `register_deployment_readiness_hook`, and
  `RUNTIME_HEALTH_ABI_VERSION` (see the [public API](./public-api.md));
- the `ms-tau` command and its workspace-bound startup behavior;
- documented `MAINSEQUENCE_TAU_*`, `MAINSEQUENCE_ENDPOINT`, `MAINSEQUENCE_AUTH_MODE`, and
  `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID`, `MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE`,
  `MAINSEQUENCE_ACCESS_TOKEN`, `MAINSEQUENCE_REFRESH_TOKEN`, `MAINSEQUENCE_CLI`, `TAU_LOCAL_*`,
  `TAU_EXCLUDE_BASE_TOOLS`, and `TAU_EXCLUDE_MAINSEQUENCE_MCP` settings, and the platform's
  hosting settings `MAINSEQUENCE_CALLER_AUTH_MODE`, `MAINSEQUENCE_CALLER_ASSERTION_ISSUER`,
  `MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL`, `MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID`,
  `APP_NAME`, and `FASTAPI_PUBLIC_BASE_URL`;
- the request-identity declaration `create_app` sets for the platform launcher;
- documented health, readiness, chat, response, session, and A2A HTTP/wire behavior;
- packaged Tau defaults and Tau-native project `.tau` precedence; and
- the documented absence of SDK-owned container/deployment artifacts and optional tool baggage.

A runtime the platform hosts admits a request only with the platform's signed assertion, and lets
only a session's owner, an Organization admin, or the Agent that delegated to it address that
session (ADR 0019). Callers of a
hosted runtime go through the platform, which forwards the assertion; platform probes use the
launcher's own endpoints. A runtime that is not hosted handles requests as before.

The documented local-mode workflow is one `mainsequence login` and no token in the project `.env`:
local mode asks the Main Sequence CLI for its access token. The `MAINSEQUENCE_ACCESS_TOKEN` and
`MAINSEQUENCE_REFRESH_TOKEN` pair stays supported in the process environment, as the alternative
for launchers and CI. Reading that pair from the project `.env` file is deprecated. It still works
and startup logs a warning.

A major release is required to remove or incompatibly change one of those surfaces. Minor releases
may add backward-compatible settings, APIs, routes, or behavior. Patch releases contain compatible
fixes. Every release records relevant changes in the changelog and passes the same distribution,
consumer, project-configuration, and protocol contracts.

## Requester and MCP availability

Requester access depends on the platform's enabled operations and the Agent's administrator
configuration. An SDK upgrade does not add permissions or make an unsupported endpoint accept
requester calls. See the [Security and access guide](./security-model.md#availability).

Requester-marked MCP tools and declared-application MCP connections are currently documented under
**Unreleased** in the [changelog](../../CHANGELOG.md#unreleased). They require both a supporting SDK
and platform support. Unmarked platform tools keep their existing workload behavior during this
transition. Delegated requester access additionally requires the platform to record the requester;
an SDK release alone cannot provide that record.

## Branch and Release Standard

| Branch | Role | Publishes to PyPI |
| --- | --- | --- |
| `feat/*` | work in progress | nothing |
| `development` | where features land, by merge or direct push; nothing is tagged here | `X.Y.Z.devN`, automatically |
| `main` | receives `development` when a release is decided, through a pull request | `X.Y.Z`, automatically, when the pull request is merged |

A merge to `main` is the release. The
[publish workflow](../../.github/workflows/publish-to-pipy.yml) runs on the merge, publishes the
version `pyproject.toml` declares, creates the tag `vX.Y.Z` and the GitHub release itself, and
raises the patch number on `development`, so no `X.Y.Z.devN` follows the final `X.Y.Z`. A tag pushed
by hand publishes nothing. `development` reaches `main` through a **merge commit**; a squash or
rebase would create new commits and stop the two branches sharing history, and the workflow could
no longer merge the release back into `development`.

Every push to `development` publishes one PEP 440 development release through the
[development publish workflow](../../.github/workflows/publish-development-release.yml). PyPI does
not accept a `1.2.x-dev` form; the form is `1.2.6.dev41`, which sorts before its final
(`1.2.5 < 1.2.6.dev41 < 1.2.6`) and therefore carries the number of the *next* release. The release
segment is the later of the newest final release on PyPI with its patch number raised by one, and
the version declared in `pyproject.toml`; `N` is the workflow's run number. Git tags are not used as
a base because the tag history predates this standard. Nothing is edited or tagged by hand, one
push is one development release however many commits it holds, and a newer push cancels a build
still running.

Development releases do not reach ordinary consumers: `pip` and `uv` ignore them unless one is
pinned exactly or `--pre` is passed. They carry no compatibility promise — the surfaces above are
promised by final releases only.

`pyproject.toml` is the only file that writes the version. The consumer fixture resolves the SDK
from this checkout instead of naming a version, and the contract tests read the declared version
rather than repeating it, so a computed development version stays confined to the build that
computes it.

Modules not exported by `ms_tau_sdk.__all__`, internal classes, logs beyond their documented stable
fields, and implementation-specific diagnostics are private. Importing them does not create a
compatibility promise.

Tau remains pinned exactly because its resource precedence, extension lifecycle, provider catalog,
thinking levels, and storage protocol directly affect SDK behavior. Updating Tau requires the full
configuration, provider, persistence, distribution, clean-install, and consumer-fixture gates.

Security or external-service requirements can force a breaking change. Such a change requires an
ADR, a major version, and migration documentation; silent fallback to a retired SDK mode is not
allowed.
