"""Packaged Tau resource paths with project-native override semantics."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from importlib.resources import files
from pathlib import Path
from typing import Self

from tau_coding.paths import TauPaths
from tau_coding.resources import TauResourcePaths


def resource_root() -> Path:
    return Path(str(files("ms_tau_sdk.resources")))


class _SkillsDir(Path):
    """A skills directory whose listing also holds the skills inside its group folders.

    Tau reads the folders directly in a skills directory, so the skills in a folder without a
    `SKILL.md`, such as `.agents/skills/mcp_tools/<skill>/SKILL.md`, were skipped. Listing them
    beside the direct entries lets Tau read, index, reload and serve them like any other skill.
    """

    def iterdir(self) -> Generator[Self]:
        for entry in sorted(Path(self).iterdir()):
            yield self.with_segments(entry)
            if _is_group(entry):
                yield from map(self.with_segments, _skill_folders(entry))


def _is_group(folder: Path) -> bool:
    # A symbolic link is not followed into, so a link cannot loop the listing.
    return folder.is_dir() and not folder.is_symlink() and not (folder / "SKILL.md").exists()


def _skill_folders(group: Path) -> Iterator[Path]:
    for entry in sorted(group.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").exists():
            yield entry
        elif _is_group(entry):
            yield from _skill_folders(entry)


class _NestedSkillsPaths(TauPaths):
    """Tau paths whose project skills directories read nested skills.

    Tau rebuilds its resource paths on load and reload but keeps this object, so nested skills
    are read on both.
    """

    __slots__ = ()

    def project_skills_dir(self, cwd: Path) -> Path:
        return _SkillsDir(super().project_skills_dir(cwd))

    def project_agents_skills_dir(self, cwd: Path) -> Path:
        return _SkillsDir(super().project_agents_skills_dir(cwd))


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
        paths=_NestedSkillsPaths(home=state_home),
    )
