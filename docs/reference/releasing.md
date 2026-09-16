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

The
[`Publish Python package to PyPI`](../../.github/workflows/publis-to-pipy.yaml) workflow runs only
when a `v*` tag is pushed. The tag must exactly match `v<pyproject version>` or the job fails before
publication. A valid tag builds, verifies, clean-installs, and attests the wheel and source
distribution before a protected job publishes them with the official PyPA action and trusted
publishing. The full verified bundle is also retained as a workflow artifact.

The repository must configure the protected `pypi` GitHub environment and PyPI trusted-publisher
relationship before a tag can publish. There is no stored PyPI API token. Creating or pushing a tag
is an explicit release-owner action and is not performed by the build scripts.

Consumers should pin a candidate wheel by immutable artifact and checksum during candidate review.
Published stable consumers pin the normal distribution version, for example
`ms-tau-sdk==1.0.0`.

## Compatibility Review

Before a stable tag, verify the documented public API, settings, HTTP/wire contracts, project
configuration behavior, complete test suite, fixture lock, compatibility notes, and changelog. See
the [compatibility policy](./compatibility.md), [ownership boundary](./ownership.md), and
[test gates](./testing.md).
