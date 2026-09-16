from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_distribution_metadata_and_sdist_allowlist_are_explicit() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["name"] == "ms-tau-sdk"
    assert project["project"]["version"] == "1.0.0"
    assert "Development Status :: 5 - Production/Stable" in project["project"]["classifiers"]
    assert project["project"]["readme"] == "README.md"
    assert project["project"]["urls"]["Repository"].endswith("/astro-tau")
    assert project["tool"]["hatch"]["build"]["targets"]["sdist"]["include"] == [
        "/CHANGELOG.md",
        "/README.md",
        "/pyproject.toml",
        "/src/ms_tau_sdk",
    ]


def test_release_automation_builds_inspects_clean_installs_and_uses_oidc() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "scripts/verify_distribution.py" in workflow
    assert "scripts/verify_clean_install.py" in workflow
    assert "--require-clean-source" in workflow
    assert "actions/attest-build-provenance@" in workflow
    assert "astral-sh/attest-action@" in workflow
    assert "uv build --no-sources" in workflow
    assert "uv publish --trusted-publishing always" in workflow
    assert "id-token: write" in workflow
    assert "docker" not in workflow.lower()
