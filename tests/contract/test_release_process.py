from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FINAL_VERSION = re.compile(r"\d+\.\d+\.\d+")


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


def test_the_declared_version_heads_the_changelog() -> None:
    # The declared version and the changelog are the two places a release is written down by hand,
    # and this repository has published tags that disagreed with both. Notes for work that has not
    # been released yet collect under `Unreleased`.
    version = _project()["project"]["version"]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    headings = [
        heading
        for heading in re.findall(r"^## (\S+)", changelog, flags=re.MULTILINE)
        if heading != "Unreleased"
    ]

    assert headings[:1] == [version]


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
