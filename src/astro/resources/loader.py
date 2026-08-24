"""Main Sequence prompt policy for Tau."""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path


def resource_root() -> Path:
    return Path(str(files("astro.resources")))


def append_system_prompt(*, extra_context: str = "") -> str:
    sections = [(resource_root() / "APPEND_SYSTEM.md").read_text(encoding="utf-8").strip()]
    if os.getenv("MAINSEQUENCE_CHILD_RUNTIME", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        agent = os.getenv("MAINSEQUENCE_ACTIVE_AGENT", "unknown")
        child = (resource_root() / "CHILD_POLICY.md").read_text(encoding="utf-8").strip()
        sections.append(f"# Main Sequence child runtime policy\nCurrent agent: {agent}\n\n{child}")
    if extra_context.strip():
        sections.append(extra_context.strip())
    return "\n\n".join(sections)
