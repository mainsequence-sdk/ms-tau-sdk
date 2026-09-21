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

## Optional Tau Board distribution

`packages/tau-board/` builds the independent `ms-tau-board` distribution. It has its own
`tau-board` command and does not import `ms-tau-sdk`. Build and inspect it with:

```bash
uv build --no-sources packages/tau-board --out-dir board-dist
uv run python packages/tau-board/scripts/verify_distribution.py board-dist
```

The board verifier checks wheel and source-distribution contents, its small dependency set, the
packaged Bulma license, and the raw and compressed UI asset budgets. The root SDK verifier rejects
board source and UI assets from the SDK distribution. The SDK's `tau-board` extra declares a
compatible published board version.

Push a `tau-board-v<board version>` tag to run
[`Publish Tau Board to PyPI`](../../.github/workflows/publish-tau-board.yml). Configure the protected
`pypi-tau-board` GitHub environment and a PyPI trusted publisher for that workflow first. Publish
the matching board version before publishing an SDK version that advertises the extra: both SDK
release workflows check that the board requirement resolves from PyPI. A branch build does not
publish either distribution.

## Registry Publication

Final releases and development releases are published by two different workflows. The
[branch and release standard](./compatibility.md#branch-and-release-standard) states which branch
does which.

### Final releases from a merge to `main`

A merge to `main` is the release. The
[`Publish Python package to PyPI`](../../.github/workflows/publish-to-pipy.yml) workflow runs on
every push to `main`, and `main` only changes through a pull request, so every run is a release
merge. Nobody pushes a tag, and a tag pushed by hand publishes nothing. The workflow:

1. reads the version `pyproject.toml` declares and fails when PyPI already has it ("a merge to main
   is a release and must carry the next version");
2. fails when a tag `vX.Y.Z` already exists on another commit;
3. requires that version to head `CHANGELOG.md` (rename `Unreleased` to `X.Y.Z — date` in the
   release change);
4. builds, verifies, clean-installs, and attests the wheel and source distribution, then a protected
   job publishes them with the official PyPA action and trusted publishing. The full verified
   bundle is also retained as a workflow artifact;
5. creates the tag `vX.Y.Z` and the GitHub release on the merge commit, after the upload, so a tag
   always names code that is on PyPI;
6. merges the release commit into `development`, raises the patch number there, and pushes both in
   one push (job `Declare the next version on development`).

A failure in steps 1 to 3 publishes nothing. Do not push `main` back to `development` by hand: a
push by hand before the patch number is raised would publish one more `X.Y.Z.devN` of a version
that is already final. A pull request into `main` that does not raise the version, a hotfix for
example, fails at step 1; raise the version in it.

The repository must configure the protected `pypi` GitHub environment and PyPI trusted-publisher
relationship before a merge can publish. There is no stored PyPI API token. The tag ruleset
"release tags v\*: admins only" must list GitHub Actions as a bypass actor, because the workflow
creates the tag with the workflow token; without it the `tag` job fails after the upload, and the
tag and the GitHub release are missing until the job is re-run. Merging the release pull request is
the explicit release-owner action; the build scripts never publish.

### Development releases from `development`

Every push to `development` runs the
[`Publish development release to PyPI`](../../.github/workflows/publish-development-release.yml)
workflow. It runs the same quality gate as [`quality.yml`](../../.github/workflows/quality.yml)
first and publishes nothing when the gate fails. Only then does
[`scripts/compute_development_version.py`](../../scripts/compute_development_version.py) write
`X.Y.Z.devN` into `pyproject.toml`, where `X.Y.Z` is the version `pyproject.toml` declares, and the workflow refuses to continue if any other tracked file
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

`pyproject.toml` is the only source of the version. On `development` it declares the release being
worked toward: while it says `1.2.6`, development releases are `1.2.6.devN` and the merge to `main`
publishes `1.2.6`. PyPI is read only as a guard: when the declared version is already released, the
development build fails with "development must declare the next release" instead of publishing under
a number the repository does not show. After a final release is published, the release workflow
raises the patch number on `development` by itself (job `Declare the next version on development`),
so once `1.2.6` is released the next development release is `1.2.7.devN` and there is no further
`1.2.6.devN`; that commit is pushed with the workflow token and therefore starts no development
release of its own. A minor or major release is declared by hand, by writing that version on `development`.

`pyproject.toml` is the only file that declares the version. The consumer fixture in
`tests/fixtures/sdk-consumer-project` resolves the SDK from this checkout rather than naming a
version, and the contract tests read the declared version, so a version bump touches the changelog
and `pyproject.toml` and nothing else.

## Compatibility Review

Before a release merge, verify the documented public API, settings, HTTP/wire contracts, project
configuration behavior, complete test suite, fixture lock, compatibility notes, and changelog. See
the [compatibility policy](./compatibility.md), [ownership boundary](./ownership.md), and
[test gates](./testing.md).
