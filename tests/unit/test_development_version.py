from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/compute_development_version.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("compute_development_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


development_version = _module()


def test_the_base_is_the_newest_published_final_with_its_patch_raised() -> None:
    version = development_version.next_development_version(
        published=[(1, 2, 3), (1, 2, 5), (1, 1, 9)],
        declared=(1, 2, 5),
        run_number=41,
    )

    assert version == "1.2.6.dev41"


def test_a_planned_release_ahead_of_pypi_keeps_its_own_number() -> None:
    version = development_version.next_development_version(
        published=[(1, 2, 5)],
        declared=(1, 3, 0),
        run_number=42,
    )

    # `1.2.6.dev42` would sort before `1.3.0`, so the development releases leading up to a planned
    # minor or major release would never be tried against the number they precede.
    assert version == "1.3.0.dev42"


def test_an_unpublished_distribution_falls_back_to_the_declared_version() -> None:
    version = development_version.next_development_version(
        published=[],
        declared=(0, 1, 0),
        run_number=1,
    )

    assert version == "0.1.0.dev1"


def test_prereleases_and_fully_yanked_releases_are_not_a_base() -> None:
    payload = {
        "releases": {
            "1.2.5": [{"yanked": False}],
            "1.2.6": [{"yanked": True}, {"yanked": True}],
            "1.3.0rc1": [{"yanked": False}],
            "1.3.0.dev7": [{"yanked": False}],
            "2.0": [{"yanked": False}],
            "1.2.4": [],
        }
    }

    assert sorted(development_version.final_releases(payload)) == [(1, 2, 4), (1, 2, 5)]


def test_a_partially_yanked_release_still_counts() -> None:
    payload = {"releases": {"1.2.5": [{"yanked": True}, {"yanked": False}]}}

    assert development_version.final_releases(payload) == [(1, 2, 5)]


def test_a_non_final_declared_version_is_rejected() -> None:
    with pytest.raises(development_version.DevelopmentVersionError):
        development_version.declared_release("1.2.6.dev41")


def test_a_negative_run_number_is_rejected() -> None:
    with pytest.raises(development_version.DevelopmentVersionError):
        development_version.next_development_version(
            published=[(1, 2, 5)],
            declared=(1, 2, 5),
            run_number=-1,
        )


def test_writing_the_version_touches_only_the_project_version_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = tomllib.loads(original)["project"]["version"]
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(original, encoding="utf-8")
    monkeypatch.setattr(development_version, "PYPROJECT", pyproject)

    development_version.apply_version("9.9.9.dev41")

    rewritten = pyproject.read_text(encoding="utf-8")
    assert tomllib.loads(rewritten)["project"]["version"] == "9.9.9.dev41"
    changed = [
        (before, after)
        for before, after in zip(original.splitlines(), rewritten.splitlines(), strict=True)
        if before != after
    ]
    assert changed == [(f'version = "{declared}"', 'version = "9.9.9.dev41"')]


def test_the_declared_version_round_trips_through_the_computed_one() -> None:
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]

    release = development_version.declared_release(declared)

    assert (
        development_version.next_development_version(published=[], declared=release, run_number=0)
        == f"{declared}.dev0"
    )
