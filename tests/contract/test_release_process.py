from __future__ import annotations

import importlib.util
import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
FINAL_VERSION = re.compile(r"\d+\.\d+\.\d+")
VERIFY_DISTRIBUTION = ROOT / "scripts/verify_distribution.py"


def _verify_distribution() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_distribution", VERIFY_DISTRIBUTION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_wheel(wheel: Path, extra_paths: list[str]) -> None:
    """Write a wheel that is valid apart from the paths under test."""
    required = _verify_distribution().REQUIRED_WHEEL_PATHS
    with zipfile.ZipFile(wheel, "w") as archive:
        for path in sorted(set(required) | set(extra_paths)):
            archive.writestr(path, "")
        archive.writestr("ms_tau_sdk-1.2.5.dist-info/METADATA", "")
        archive.writestr(
            "ms_tau_sdk-1.2.5.dist-info/entry_points.txt",
            "[console_scripts]\nms-tau = ms_tau_sdk.cli:main\n",
        )


def _write_sdist(sdist: Path, paths: list[str]) -> None:
    with tarfile.open(sdist, "w:gz") as archive:
        for path in paths:
            archive.addfile(tarfile.TarInfo(name=path))


def _project() -> dict[str, object]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_distribution_metadata_and_sdist_allowlist_are_explicit() -> None:
    project = _project()

    assert project["project"]["name"] == "ms-tau-sdk"
    # The version is declared here and nowhere else; the changelog records it and the development
    # publish workflow computes `X.Y.Z.devN` from it, so no other file repeats the literal.
    assert FINAL_VERSION.fullmatch(project["project"]["version"])
    assert "Development Status :: 5 - Production/Stable" in project["project"]["classifiers"]
    assert project["project"]["readme"] == "README.md"
    assert project["project"]["urls"]["Repository"] == (
        "https://github.com/mainsequence-sdk/ms-tau-sdk"
    )
    assert project["tool"]["hatch"]["build"]["targets"]["sdist"]["include"] == [
        "/CHANGELOG.md",
        "/README.md",
        "/pyproject.toml",
        "/src/ms_tau_sdk",
    ]


def test_the_changelog_never_runs_ahead_of_the_declared_version() -> None:
    # `development` declares the release being worked toward, so between releases the newest dated
    # changelog section is the previous release and new notes collect under `Unreleased`. At release
    # time `Unreleased` becomes the declared version, and the release workflow requires exactly that
    # before it publishes.
    version = _project()["project"]["version"]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    headings = [
        heading
        for heading in re.findall(r"^## (\S+)", changelog, flags=re.MULTILINE)
        if heading != "Unreleased"
    ]

    def release(value: str) -> tuple[int, ...]:
        return tuple(int(part) for part in value.split("."))

    assert headings, "the changelog has no released section"
    assert release(headings[0]) <= release(version)


def test_release_automation_builds_inspects_clean_installs_and_uses_oidc() -> None:
    workflow_path = ROOT / ".github/workflows/publish-to-pipy.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert workflow_path.is_file()
    assert not (ROOT / ".github/workflows/release.yml").exists()
    assert not (ROOT / ".github/workflows/publis-to-pipy.yaml").exists()
    assert "scripts/verify_distribution.py" in workflow
    assert "scripts/verify_clean_install.py" in workflow
    assert "--require-clean-source" in workflow
    assert "actions/attest-build-provenance@" in workflow
    assert "uv build --no-sources" in workflow
    assert "pypa/gh-action-pypi-publish@" in workflow
    assert 'tags:\n      - "v*"' in workflow
    assert "name: pypi" in workflow
    assert "id-token: write" in workflow
    assert "uv publish" not in workflow
    assert "astral-sh/attest-action@" not in workflow
    assert "docker" not in workflow.lower()


def test_a_final_release_tag_must_be_contained_in_main() -> None:
    workflow = (ROOT / ".github/workflows/publish-to-pipy.yml").read_text(encoding="utf-8")

    # A `vX.Y.Z` tag on a commit outside `main` must publish nothing, so the guard runs before any
    # build step and the checkout has to fetch enough history to answer the containment question.
    assert "fetch-depth: 0" in workflow
    assert 'git merge-base --is-ancestor "$RELEASE_SHA" refs/remotes/origin/main' in workflow
    guard = workflow.index("Require the release tag to be contained in main")
    assert guard < workflow.index("uv build --no-sources")


def test_development_pushes_publish_a_gated_development_release() -> None:
    workflow_path = ROOT / ".github/workflows/publish-development-release.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert workflow_path.is_file()
    assert "branches:\n      - development\n" in workflow
    assert "tags:" not in workflow
    assert "cancel-in-progress: true" in workflow

    # The quality gate from `quality.yml` runs first, so a failing commit publishes nothing.
    for gate in (
        "uv run ruff format --check src tests scripts",
        "uv run ruff check src tests scripts",
        "uv run mypy",
        "uv run pytest",
        "uv sync --frozen",
    ):
        assert gate in workflow
    assert workflow.index("uv run pytest") < workflow.index(
        "scripts/compute_development_version.py"
    )

    # A gate that imports the SDK leaves caches and runtime state under `src`, and Hatchling
    # packages whatever it finds there, so the tree is restored before anything is built.
    assert "git clean -xfdq src" in workflow
    assert workflow.index("uv run pytest") < workflow.index("git clean -xfdq src")
    assert workflow.index("git clean -xfdq src") < workflow.index("uv build --no-sources")

    # The computed version is the only change a development build may make to the tracked tree.
    assert "--write --github-output" in workflow
    assert 'if [ "$changed" != " M pyproject.toml" ]; then' in workflow
    assert "--require-clean-source" not in workflow
    assert "scripts/verify_distribution.py" in workflow
    assert "scripts/verify_clean_install.py" in workflow
    assert "actions/attest-build-provenance@" in workflow
    assert "pypa/gh-action-pypi-publish@" in workflow
    assert "id-token: write" in workflow
    assert "uv publish" not in workflow


def test_quality_workflow_and_release_workflows_cover_the_release_branches() -> None:
    quality = (ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert "- development\n" in quality
    assert "- main\n" in quality


def test_the_release_gate_allowlists_what_the_package_actually_ships() -> None:
    verify = _verify_distribution()
    packaged = {
        path.name for path in (ROOT / "src/ms_tau_sdk").iterdir() if path.name != "__pycache__"
    }

    # Growing the package is a deliberate release-contract change: the gate and the
    # source tree have to agree on what a distribution may contain.
    assert verify.ALLOWED_PACKAGE_ENTRIES == packaged
    assert verify.ALLOWED_PACKAGE_SUFFIXES == {".md", ".py"}
    assert "state" in verify.FORBIDDEN_PARTS


def test_the_release_gate_rejects_runtime_state_inside_a_wheel(tmp_path) -> None:
    verify = _verify_distribution()
    wheel = tmp_path / "ms_tau_sdk-1.2.5-py3-none-any.whl"
    _write_wheel(wheel, ["ms_tau_sdk/resources/state/extensions/llama.cpp.json.lock"])

    with pytest.raises(verify.DistributionError) as error:
        verify._validate_wheel(
            wheel,
            distribution="ms-tau-sdk",
            version="1.2.5",
            requires_python=">=3.13",
            dependency_count=12,
        )

    assert "state/extensions/llama.cpp.json.lock" in str(error.value)


def test_the_release_gate_rejects_unlisted_package_paths_in_a_wheel(tmp_path) -> None:
    verify = _verify_distribution()
    wheel = tmp_path / "ms_tau_sdk-1.2.5-py3-none-any.whl"
    _write_wheel(wheel, ["ms_tau_sdk/resources/models-store.json", "ms_tau_sdk/scratch/notes.md"])

    with pytest.raises(verify.DistributionError) as error:
        verify._validate_wheel(
            wheel,
            distribution="ms-tau-sdk",
            version="1.2.5",
            requires_python=">=3.13",
            dependency_count=12,
        )

    message = str(error.value)
    assert "outside the release allowlist" in message
    assert "models-store.json" in message
    assert "scratch" in message


def test_the_release_gate_rejects_unlisted_package_paths_in_an_sdist(tmp_path) -> None:
    verify = _verify_distribution()
    sdist = tmp_path / "ms_tau_sdk-1.2.5.tar.gz"
    _write_sdist(
        sdist,
        [
            "ms_tau_sdk-1.2.5/README.md",
            "ms_tau_sdk-1.2.5/pyproject.toml",
            "ms_tau_sdk-1.2.5/src/ms_tau_sdk/__init__.py",
            "ms_tau_sdk-1.2.5/src/ms_tau_sdk/resources/credentials.json",
        ],
    )

    with pytest.raises(verify.DistributionError) as error:
        verify._validate_sdist(sdist, distribution="ms-tau-sdk", version="1.2.5")

    assert "credentials.json" in str(error.value)
