# Compatibility Policy

Version `1.0.0` establishes the first stable Main Sequence TAU SDK contract. The project follows
semantic versioning for these supported surfaces:

- `ms_tau_sdk.create_app`, `ms_tau_sdk.TauSDKSettings`, and `ms_tau_sdk.__version__`;
- the `ms-tau` command and its workspace-bound startup behavior;
- documented `MAINSEQUENCE_TAU_*`, `MAINSEQUENCE_ENDPOINT`, `MAINSEQUENCE_AUTH_MODE`, and
  `MAINSEQUENCE_RUNTIME_CREDENTIAL_*`, `MAINSEQUENCE_ACCESS_TOKEN`,
  `MAINSEQUENCE_REFRESH_TOKEN`, `TAU_LOCAL_*`, `TAU_EXCLUDE_BASE_TOOLS`, and
  `TAU_EXCLUDE_MAINSEQUENCE_MCP` settings;
- documented health, readiness, chat, response, session, and A2A HTTP/wire behavior;
- packaged Tau defaults and Tau-native project `.tau` precedence; and
- the documented absence of SDK-owned container/deployment artifacts and optional tool baggage.

A major release is required to remove or incompatibly change one of those surfaces. Minor releases
may add backward-compatible settings, APIs, routes, or behavior. Patch releases contain compatible
fixes. Every release records relevant changes in the changelog and passes the same distribution,
consumer, project-configuration, and protocol contracts.

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
