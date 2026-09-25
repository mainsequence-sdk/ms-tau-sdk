#!/usr/bin/env python3
"""Validate SDK release artifacts and emit release metadata."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from datetime import UTC, datetime
from email.message import Message
from email.parser import BytesParser
from email.policy import compat32
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
FORBIDDEN_PARTS = {
    "__pycache__",
    "astro",
    "deployment",
    "docker",
    "kubernetes",
    "mainsequence_astro",
    # Tau runtime state. The SDK writes it to a workspace state root, never into
    # the package; a `state/` directory inside an artifact means a build picked
    # up runtime residue from the source tree.
    "state",
    "tests",
}
IMPORT_PACKAGE = "ms_tau_sdk"
BOARD_PACKAGE = "ms_tau_board"
BOARD_SOURCE = PurePosixPath("packages/tau-board/src/ms_tau_board")
BOARD_MODULES = {
    "__init__.py",
    "app.py",
    "cli.py",
    "config.py",
    "env_file.py",
    "logs.py",
    "proxy.py",
    "state.py",
}
BOARD_ASSETS = {
    "index.html",
    "app.js",
    "board.css",
    "bulma.min.css",
    "BULMA-LICENSE.txt",
}
# Everything the package legitimately ships, as the entries directly below
# `ms_tau_sdk/`. Anything else in an artifact is residue, so adding a module or
# data directory to the package is a deliberate release-contract change here.
ALLOWED_PACKAGE_ENTRIES = {
    "__init__.py",
    "agent_skills",
    "api",
    "app.py",
    "application.py",
    "backend",
    "cli.py",
    "errors.py",
    "logging.py",
    "protocols",
    "providers",
    "resources",
    "runtime",
    "sessions",
    "settings.py",
    "skills.py",
    "tools",
}
# The package is source plus markdown resources. Runtime state arrives as
# something else entirely (`.lock`, `.json`, `.jsonl`, `.sqlite3`), so an
# unexpected suffix fails the gate even under an allowed entry.
ALLOWED_PACKAGE_SUFFIXES = {".md", ".py"}
REQUIRED_WHEEL_PATHS = {
    "ms_tau_sdk/__init__.py",
    "ms_tau_sdk/agent_skills/tau_a2a_runtime_adapter/SKILL.md",
    "ms_tau_sdk/agent_skills/tau_local_development/SKILL.md",
    "ms_tau_sdk/agent_skills/tau_project_customization/SKILL.md",
    "ms_tau_sdk/agent_skills/tau_repository_integration/SKILL.md",
    "ms_tau_sdk/cli.py",
    "ms_tau_sdk/resources/SYSTEM.md",
    "ms_tau_sdk/resources/prompts/review-code-repository.md",
    "ms_tau_sdk/skills.py",
    *(f"ms_tau_board/{name}" for name in BOARD_MODULES),
    *(f"ms_tau_board/static/{name}" for name in BOARD_ASSETS),
}


class DistributionError(RuntimeError):
    """Raised when a release artifact violates the SDK distribution contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _normalized_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _find_artifacts(dist_dir: Path, distribution: str, version: str) -> tuple[Path, Path]:
    stem = f"{_normalized_distribution(distribution)}-{version}"
    wheels = sorted(dist_dir.glob(f"{stem}-*.whl"))
    sdists = sorted(dist_dir.glob(f"{stem}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise DistributionError(
            f"expected one wheel and one sdist for {distribution} {version}; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s) in {dist_dir}"
        )
    return wheels[0], sdists[0]


def _forbidden_path(path: str) -> bool:
    normalized = path.lower().replace("-", "_")
    parts = set(PurePosixPath(normalized).parts)
    return bool(parts & FORBIDDEN_PARTS) or normalized.endswith((".pyc", ".pyo"))


def _package_allowlist_violation(relative: PurePosixPath) -> str | None:
    """Return why a path below the import package is not part of the release.

    `relative` is the path relative to the directory holding `ms_tau_sdk`, so
    its first part is always the import package itself.
    """
    entries = relative.parts[1:]
    if not entries:
        return "the import package must be a directory"
    if entries[0] not in ALLOWED_PACKAGE_ENTRIES:
        return (
            f"{entries[0]!r} is not a packaged entry; add it to "
            "ALLOWED_PACKAGE_ENTRIES when the package really grew"
        )
    if relative.suffix not in ALLOWED_PACKAGE_SUFFIXES:
        return f"{relative.suffix or relative.name!r} is not a packaged file type"
    return None


def _board_allowlist_violation(relative: PurePosixPath) -> str | None:
    entries = relative.parts[1:]
    if not entries:
        return "the board import package must be a directory"
    if entries[0] == "static":
        if len(entries) != 2 or entries[1] not in BOARD_ASSETS:
            return "unlisted board asset"
    elif len(entries) != 1 or entries[0] not in BOARD_MODULES:
        return "unlisted board module"
    return None


def _verify_board_assets(archive: zipfile.ZipFile) -> None:
    assets = [archive.read(f"{BOARD_PACKAGE}/static/{name}") for name in BOARD_ASSETS]
    if sum(map(len, assets)) >= 800 * 1024:
        raise DistributionError("Board assets exceed the 800 KiB raw limit")
    if sum(len(gzip.compress(value)) for value in assets) >= 120 * 1024:
        raise DistributionError("Board assets exceed the 120 KiB gzip limit")
    if len(archive.read(f"{BOARD_PACKAGE}/static/app.js")) >= 56 * 1024:
        raise DistributionError("Board JavaScript exceeds the 56 KiB limit")
    html = archive.read(f"{BOARD_PACKAGE}/static/index.html").decode("utf-8")
    if re.search(r"(?:src|href)=[\"']https?://", html):
        raise DistributionError("Board HTML loads a remote asset")


def _metadata_from_wheel(archive: zipfile.ZipFile) -> Message:
    metadata_paths = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
    if len(metadata_paths) != 1:
        raise DistributionError("wheel must contain exactly one METADATA file")
    return BytesParser(policy=compat32).parsebytes(archive.read(metadata_paths[0]))


def _validate_wheel(
    wheel: Path,
    *,
    distribution: str,
    version: str,
    requires_python: str,
    dependency_count: int,
) -> list[str]:
    dist_info = f"{_normalized_distribution(distribution)}-{version}.dist-info"
    with zipfile.ZipFile(wheel) as archive:
        paths = set(archive.namelist())
        forbidden = sorted(path for path in paths if _forbidden_path(path))
        if forbidden:
            raise DistributionError(f"wheel contains forbidden paths: {forbidden}")
        outside: list[str] = []
        for path in sorted(paths):
            pure = PurePosixPath(path)
            if pure.parts and pure.parts[0] == dist_info:
                continue
            if not pure.parts or pure.parts[0] not in {IMPORT_PACKAGE, BOARD_PACKAGE}:
                outside.append(f"{path} (outside allowed import packages and {dist_info}/)")
                continue
            violation = (
                _package_allowlist_violation(pure)
                if pure.parts[0] == IMPORT_PACKAGE
                else _board_allowlist_violation(pure)
            )
            if violation is not None:
                outside.append(f"{path} ({violation})")
        if outside:
            raise DistributionError(
                f"wheel contains paths outside the release allowlist: {outside}"
            )
        missing = sorted(REQUIRED_WHEEL_PATHS - paths)
        if missing:
            raise DistributionError(f"wheel is missing required paths: {missing}")
        _verify_board_assets(archive)

        entry_points = [path for path in paths if path.endswith(".dist-info/entry_points.txt")]
        if len(entry_points) != 1:
            raise DistributionError("wheel must contain exactly one entry_points.txt")
        entry_point_text = archive.read(entry_points[0]).decode("utf-8")
        if "ms-tau = ms_tau_sdk.cli:main" not in entry_point_text:
            raise DistributionError("wheel does not expose the ms-tau command")
        if "tau-board = ms_tau_board.cli:main" not in entry_point_text:
            raise DistributionError("wheel does not expose the tau-board command")

        metadata = _metadata_from_wheel(archive)

    expected_metadata = {
        "Name": distribution,
        "Version": version,
        "Requires-Python": requires_python,
    }
    for key, expected in expected_metadata.items():
        actual = metadata.get(key)
        if actual != expected:
            raise DistributionError(f"wheel {key} is {actual!r}; expected {expected!r}")

    dependencies = metadata.get_all("Requires-Dist", [])
    if len(dependencies) != dependency_count:
        raise DistributionError(
            f"wheel declares {len(dependencies)} dependencies; expected {dependency_count}"
        )
    if not any(
        requirement.replace(" ", "").lower() == "tau-ai==0.4.2" for requirement in dependencies
    ):
        raise DistributionError("wheel must retain the exact tau-ai==0.4.2 contract")
    if metadata.get("Description-Content-Type") != "text/markdown":
        raise DistributionError("wheel README metadata must be text/markdown")
    return dependencies


def _validate_sdist(sdist: Path, *, distribution: str, version: str) -> None:
    expected_root = f"{_normalized_distribution(distribution)}-{version}"
    allowed_root_files = {
        ".gitignore",  # Hatchling always includes the VCS ignore file in source distributions.
        "CHANGELOG.md",
        "PKG-INFO",
        "README.md",
        "pyproject.toml",
    }
    with tarfile.open(sdist, mode="r:gz") as archive:
        paths = [member.name for member in archive.getmembers() if member.isfile()]

    if not paths:
        raise DistributionError("sdist is empty")
    invalid: list[str] = []
    for path in paths:
        pure = PurePosixPath(path)
        if not pure.parts or pure.parts[0] != expected_root or _forbidden_path(path):
            invalid.append(path)
            continue
        relative = PurePosixPath(*pure.parts[1:])
        if str(relative) in allowed_root_files:
            continue
        if relative.parts[:1] == ("src",):
            packaged = PurePosixPath(*relative.parts[1:])
            violation = (
                _package_allowlist_violation(packaged)
                if packaged.parts[:1] == (IMPORT_PACKAGE,)
                else "unlisted source package"
            )
        elif relative.parts[:3] == BOARD_SOURCE.parts[:3]:
            if relative.parts[:4] != BOARD_SOURCE.parts[:4]:
                violation = "unlisted board source path"
            else:
                packaged = PurePosixPath(*relative.parts[3:])
                violation = _board_allowlist_violation(packaged)
        else:
            violation = "unlisted source path"
        if violation is not None:
            invalid.append(f"{path} ({violation})")
    if invalid:
        raise DistributionError(f"sdist contains paths outside the release allowlist: {invalid}")

    required = {
        f"{expected_root}/README.md",
        f"{expected_root}/pyproject.toml",
        f"{expected_root}/src/ms_tau_sdk/__init__.py",
        f"{expected_root}/src/ms_tau_sdk/agent_skills/tau_local_development/SKILL.md",
        f"{expected_root}/src/ms_tau_sdk/resources/SYSTEM.md",
        f"{expected_root}/src/ms_tau_sdk/skills.py",
        f"{expected_root}/{BOARD_SOURCE}/__init__.py",
        *(f"{expected_root}/{BOARD_SOURCE}/static/{name}" for name in BOARD_ASSETS),
    }
    missing = sorted(required - set(paths))
    if missing:
        raise DistributionError(f"sdist is missing required paths: {missing}")


def _git_output(*args: str) -> str | None:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() or None if completed.returncode == 0 else None


def _write_release_metadata(
    dist_dir: Path,
    *,
    project: dict[str, Any],
    wheel: Path,
    sdist: Path,
    dependencies: list[str],
    require_clean_source: bool,
) -> None:
    package = project["project"]
    dependency_document = {
        "distribution": package["name"],
        "requires_dist": sorted(dependencies),
        "requires_python": package["requires-python"],
        "version": package["version"],
    }
    dependencies_path = dist_dir / "DEPENDENCIES.json"
    dependencies_path.write_text(
        json.dumps(dependency_document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    revision = _git_output("rev-parse", "HEAD")
    status = _git_output("status", "--porcelain", "--untracked-files=no")
    if require_clean_source and status:
        raise DistributionError("release provenance requires a clean tracked source tree")
    provenance = {
        "artifacts": {
            wheel.name: {"sha256": _sha256(wheel), "size": wheel.stat().st_size},
            sdist.name: {"sha256": _sha256(sdist), "size": sdist.stat().st_size},
        },
        "build_backend": project["build-system"]["build-backend"],
        "distribution": package["name"],
        "generated_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "source": {
            "dirty": bool(status),
            "ref": os.environ.get("GITHUB_REF"),
            "repository": package.get("urls", {}).get("Repository"),
            "revision": revision,
        },
        "version": package["version"],
    }
    provenance_path = dist_dir / "PROVENANCE.json"
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    checksummed = [wheel, sdist, dependencies_path, provenance_path]
    checksum_lines = [f"{_sha256(path)}  {path.name}" for path in sorted(checksummed)]
    (dist_dir / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "release-dist")
    parser.add_argument("--require-clean-source", action="store_true")
    parser.add_argument("--write-release-metadata", action="store_true")
    args = parser.parse_args()

    project = _project()
    package = project["project"]
    dist_dir = args.dist_dir.resolve()
    wheel, sdist = _find_artifacts(dist_dir, package["name"], package["version"])
    dependencies = _validate_wheel(
        wheel,
        distribution=package["name"],
        version=package["version"],
        requires_python=package["requires-python"],
        dependency_count=(
            len(package["dependencies"])
            + sum(len(items) for items in package.get("optional-dependencies", {}).values())
        ),
    )
    _validate_sdist(sdist, distribution=package["name"], version=package["version"])
    if args.write_release_metadata:
        _write_release_metadata(
            dist_dir,
            project=project,
            wheel=wheel,
            sdist=sdist,
            dependencies=dependencies,
            require_clean_source=args.require_clean_source,
        )
    print(f"verified {wheel.name} and {sdist.name}")


if __name__ == "__main__":
    try:
        main()
    except DistributionError as error:
        raise SystemExit(f"distribution verification failed: {error}") from error
