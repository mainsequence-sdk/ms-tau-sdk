from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

from fastapi import FastAPI

from ms_tau_sdk.settings import get_settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests/fixtures/sdk-consumer-project"


def test_fixture_locks_the_sdk_from_this_checkout_without_repeating_its_version() -> None:
    project = tomllib.loads((FIXTURE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((FIXTURE_ROOT / "uv.lock").read_text(encoding="utf-8"))

    # The fixture names no version: `pyproject.toml` is the only place a version is written, so a
    # computed development version does not have to be threaded through the fixture as well.
    assert project["project"]["dependencies"] == ["ms-tau-sdk"]
    assert project["tool"]["uv"]["sources"]["ms-tau-sdk"] == {
        "path": "../../..",
        "editable": False,
    }

    locked = {package["name"]: package for package in lock["package"]}
    assert locked["ms-tau-sdk"]["source"] == {"directory": "../../../"}
    assert (FIXTURE_ROOT / locked["ms-tau-sdk"]["source"]["directory"]).resolve() == (
        REPOSITORY_ROOT
    )


def test_fixture_shim_builds_the_sdk_app_from_its_workspace(monkeypatch) -> None:
    monkeypatch.chdir(FIXTURE_ROOT)
    monkeypatch.setenv("MAINSEQUENCE_TAU_STARTUP_DEPENDENCIES_ENABLED", "false")
    get_settings.cache_clear()
    try:
        module_path = FIXTURE_ROOT / "api/tau/main.py"
        spec = importlib.util.spec_from_file_location("consumer_fixture_tau_app", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert isinstance(module.app, FastAPI)
        assert module.app.title == "Main Sequence TAU SDK"
        assert module.app.state.settings.workspace == FIXTURE_ROOT.resolve()
    finally:
        get_settings.cache_clear()
