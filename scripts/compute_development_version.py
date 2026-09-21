#!/usr/bin/env python3
"""Compute the PEP 440 development version published from the `development` branch.

`pyproject.toml` is the only source of the version. On `development` it declares the release being
worked toward, `X.Y.Z`. Every push to `development` publishes `X.Y.Z.devN`, which sorts before its
final (`1.2.5 < 1.2.6.dev41 < 1.2.6`), and merging `development` into `main` publishes the final
`X.Y.Z`, the same number. `N` is the publishing workflow's run number, so one push is one
development release.

PyPI is consulted only as a guard: a declared version that is already released means the bump that
follows every release is missing, and the build fails instead of publishing a development release
of a version that already exists. After a final release the release workflow raises the patch
number on `development` by itself.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
PYPI_PROJECT_URL = "https://pypi.org/pypi/{distribution}/json"
FINAL_RELEASE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
PROJECT_VERSION = re.compile(r'^version = "[^"]*"$', re.MULTILINE)

Release = tuple[int, int, int]


class DevelopmentVersionError(RuntimeError):
    """Raised when the next development version cannot be determined."""


def _project() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def declared_release(version: str) -> Release:
    """Return the `X.Y.Z` release segment declared in `pyproject.toml`."""

    match = FINAL_RELEASE.fullmatch(version.strip())
    if match is None:
        raise DevelopmentVersionError(
            f"pyproject.toml must declare a final X.Y.Z version; found {version!r}"
        )
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def final_releases(payload: dict[str, Any]) -> list[Release]:
    """Return every non-yanked final `X.Y.Z` release found in a PyPI project document."""

    releases: list[Release] = []
    for candidate, files in payload.get("releases", {}).items():
        match = FINAL_RELEASE.fullmatch(candidate.strip())
        if match is None:
            continue
        if files and all(bool(file.get("yanked")) for file in files):
            continue
        releases.append((int(match[1]), int(match[2]), int(match[3])))
    return releases


def published_final_releases(distribution: str, *, timeout: float = 30.0) -> list[Release]:
    """Return the final releases PyPI lists for `distribution`, or none when it is unpublished."""

    # The endpoint is a fixed https PyPI URL; only the distribution name is interpolated.
    request = urllib.request.Request(
        PYPI_PROJECT_URL.format(distribution=distribution),
        headers={"Accept": "application/json", "User-Agent": "ms-tau-sdk-release-tooling"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return []
        raise DevelopmentVersionError(f"PyPI rejected the release query: {error}") from error
    except (OSError, ValueError) as error:
        raise DevelopmentVersionError(f"could not read releases from PyPI: {error}") from error
    if not isinstance(payload, dict):
        raise DevelopmentVersionError("PyPI returned an unexpected project document")
    return final_releases(payload)


def next_development_version(
    *,
    published: list[Release],
    declared: Release,
    run_number: int,
) -> str:
    """Return `X.Y.Z.devN` for the declared release, which must not be published yet."""

    if run_number < 0:
        raise DevelopmentVersionError(f"run number must not be negative; got {run_number}")
    if published and declared <= max(published):
        newest = ".".join(str(part) for part in max(published))
        wanted = ".".join(str(part) for part in declared)
        raise DevelopmentVersionError(
            f"pyproject.toml declares {wanted}, and {newest} is already released; "
            "development must declare the next release"
        )
    major, minor, patch = declared
    return f"{major}.{minor}.{patch}.dev{run_number}"


def final_version(*, published: list[Release], declared: Release) -> str:
    """Return the `X.Y.Z` a merge to `main` releases, which must not be published yet."""

    wanted = ".".join(str(part) for part in declared)
    if published and declared <= max(published):
        newest = ".".join(str(part) for part in max(published))
        raise DevelopmentVersionError(
            f"pyproject.toml declares {wanted}, and {newest} is already released; "
            "a merge to main is a release and must carry the next version"
        )
    return wanted


def next_patch(version: str) -> str:
    """Return the version `development` declares after `version` has been released."""

    major, minor, patch = declared_release(version)
    return f"{major}.{minor}.{patch + 1}"


def apply_version(version: str) -> None:
    """Rewrite the version declared in `pyproject.toml`, leaving every other line untouched."""

    text = PYPROJECT.read_text(encoding="utf-8")
    matches = PROJECT_VERSION.findall(text)
    if len(matches) != 1:
        raise DevelopmentVersionError(
            f"expected exactly one project version line in pyproject.toml; found {len(matches)}"
        )
    PYPROJECT.write_text(PROJECT_VERSION.sub(f'version = "{version}"', text), encoding="utf-8")


def _run_number(value: str | None) -> int:
    if value is None:
        raise DevelopmentVersionError(
            "no run number given; pass --run-number or set GITHUB_RUN_NUMBER"
        )
    try:
        return int(value)
    except ValueError as error:
        raise DevelopmentVersionError(f"run number must be an integer; got {value!r}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution")
    parser.add_argument("--run-number", default=os.environ.get("GITHUB_RUN_NUMBER"))
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--bump-patch",
        action="store_true",
        help="write the next patch version into pyproject.toml; run on development after a release",
    )
    parser.add_argument(
        "--final",
        action="store_true",
        help="print the version a merge to main releases; fails when PyPI already has it",
    )
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args()

    package = _project()["project"]
    if args.bump_patch:
        bumped = next_patch(package["version"])
        apply_version(bumped)
        print(bumped)
        return
    distribution = args.distribution or package["name"]
    if args.final:
        version = final_version(
            published=published_final_releases(distribution),
            declared=declared_release(package["version"]),
        )
    else:
        version = next_development_version(
            published=published_final_releases(distribution),
            declared=declared_release(package["version"]),
            run_number=_run_number(args.run_number),
        )
    if args.write:
        apply_version(version)
    if args.github_output:
        output = os.environ.get("GITHUB_OUTPUT")
        if not output:
            raise DevelopmentVersionError("--github-output requires GITHUB_OUTPUT")
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"version={version}\n")
    print(version)


if __name__ == "__main__":
    try:
        main()
    except DevelopmentVersionError as error:
        raise SystemExit(f"development version computation failed: {error}") from error
