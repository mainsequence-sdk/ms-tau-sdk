"""Packaged Tau resource paths with project-native override semantics."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from tau_coding.resources import TauResourcePaths


def resource_root() -> Path:
    return Path(str(files("astro.resources")))


def tau_resource_paths(cwd: Path) -> TauResourcePaths:
    """Return SDK defaults plus Tau-native project resource discovery."""
    return TauResourcePaths(root=resource_root(), cwd=cwd, agents_root=None)
