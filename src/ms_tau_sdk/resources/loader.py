"""Packaged Tau resource paths with project-native override semantics."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from tau_coding.paths import TauPaths
from tau_coding.resources import TauResourcePaths


def resource_root() -> Path:
    return Path(str(files("ms_tau_sdk.resources")))


def tau_resource_paths(cwd: Path, *, state_home: Path) -> TauResourcePaths:
    """Return SDK defaults plus Tau-native project resource discovery.

    `root` stays on the packaged resources, which are read-only inputs: an
    installed wheel lives in site-packages, so the SDK must never write there.
    `paths` therefore sends every durable Tau write — built-in extension state,
    provider credentials, project trust, diagnostics, sessions — to the
    writable `state_home` instead.
    """
    return TauResourcePaths(
        root=resource_root(),
        cwd=cwd,
        agents_root=None,
        paths=TauPaths(home=state_home),
    )
