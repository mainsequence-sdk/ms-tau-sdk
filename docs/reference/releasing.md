# Python Release Process

Main Sequence TAU SDK releases are ordinary Python distributions. The release bundle contains one
wheel, one source distribution, `DEPENDENCIES.json`, `PROVENANCE.json`, and `SHA256SUMS`. It never
contains or publishes a container image.

## Candidate Gate

Build a candidate from a clean committed revision:

```bash
uv sync --frozen
uv build --no-sources --out-dir release-dist
uv run python scripts/verify_distribution.py \
  --dist-dir release-dist \
  --require-clean-source \
  --write-release-metadata
uv run python scripts/verify_clean_install.py release-dist/ms_tau_sdk-*.whl
```

The distribution verifier checks the package identity, Python requirement, dependency metadata,
Tau pin, `ms-tau` entry point, required packaged resources, and an allowlist for wheel/sdist
contents. It rejects retired namespaces, bytecode, tests, and deployment/container paths. The
release workflow also rejects tracked changes relative to the provenance commit. The
clean-install verifier creates an isolated environment and workspace outside the checkout, imports
the public API with user and source paths disabled, starts the installed `ms-tau` command, calls
health and version, and verifies graceful shutdown.

The sdist allowlist includes `.gitignore` because Hatchling always adds that file to source
distributions; it does not broaden the accepted runtime content.

The generated checksum and JSON files describe the candidate; they are not imported by the SDK.
GitHub Actions also produces an OIDC build-provenance attestation for the wheel and sdist. The
official PyPA publishing action generates and uploads the PyPI PEP 740 attestations with the Python
distributions.

## Registry Publication

Final releases and development releases are published by two different workflows. The
[branch and release standard](./compatibility.md#branch-and-release-standard) states which branch
does which.

### Final releases from a tag on `main`

The
[`Publish Python package to PyPI`](../../.github/workflows/publish-to-pipy.yml) workflow runs only
when a `v*` tag is pushed. The tagged commit must be contained in `main` and the tag must exactly
match `v<pyproject version>`, or the job fails before publication. A tag on a commit `main` does not
contain publishes nothing. A valid tag builds, verifies, clean-installs, and attests the wheel and
source distribution before a protected job publishes them with the official PyPA action and trusted
publishing. The full verified bundle is also retained as a workflow artifact.

The repository must configure the protected `pypi` GitHub environment and PyPI trusted-publisher
relationship before a tag can publish. There is no stored PyPI API token. Creating or pushing a tag
is an explicit release-owner action and is not performed by the build scripts.

### Development releases from `development`

Every push to `development` runs the
[`Publish development release to PyPI`](../../.github/workflows/publish-development-release.yml)
workflow. It runs the same quality gate as [`quality.yml`](../../.github/workflows/quality.yml)
first and publishes nothing when the gate fails. Only then does
[`scripts/compute_development_version.py`](../../scripts/compute_development_version.py) write
`X.Y.Z.devN` into `pyproject.toml`, and the workflow refuses to continue if any other tracked file
or any other line of `pyproject.toml` changed. Nothing is tagged and nothing is committed back.

The computed version makes the tracked source deliberately dirty, so the development build records
provenance without `--require-clean-source`; `PROVENANCE.json` reports `dirty: true` and names the
commit the release was built from. Final releases keep the clean-source requirement.

This workflow needs its own PyPI trusted-publisher entry — repository `mainsequence-sdk/ms-tau-sdk`,
workflow `publish-development-release.yml`, environment `pypi-development` — and that environment
must not require a reviewer, or development releases would wait for an approval that the standard
says is not needed.

To install one deliberately:

```bash
uv add "ms-tau-sdk==1.2.6.dev41"   # or: uv add --prerelease=allow ms-tau-sdk
```

`pip` and `uv` skip development releases otherwise, so no consumer picks one up by accident.

Consumers should pin a candidate wheel by immutable artifact and checksum during candidate review.
Published stable consumers pin the normal distribution version, for example
`ms-tau-sdk==1.0.0`.

`pyproject.toml` is the only file that declares the version. The consumer fixture in
`tests/fixtures/sdk-consumer-project` resolves the SDK from this checkout rather than naming a
version, and the contract tests read the declared version, so a version bump touches the changelog
and `pyproject.toml` and nothing else.

## Compatibility Review

Before a stable tag, verify the documented public API, settings, HTTP/wire contracts, project
configuration behavior, complete test suite, fixture lock, compatibility notes, and changelog. See
the [compatibility policy](./compatibility.md), [ownership boundary](./ownership.md), and
[test gates](./testing.md).
