from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

from fastapi import FastAPI

from ms_tau_sdk.settings import get_settings

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures/sdk-consumer-project"


def test_fixture_declares_and_locks_the_sdk() -> None:
    project = tomllib.loads((FIXTURE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((FIXTURE_ROOT / "uv.lock").read_text(encoding="utf-8"))

    assert project["project"]["dependencies"] == ["ms-tau-sdk==0.1.0"]
    locked = {package["name"]: package for package in lock["package"]}
    assert locked["ms-tau-sdk"]["version"] == "0.1.0"
    assert "astro" not in locked


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
