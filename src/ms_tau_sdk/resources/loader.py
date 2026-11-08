"""Packaged Tau resource paths with project-native override semantics."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from importlib.resources import files
from pathlib import Path
from typing import ClassVar, Self

from tau_coding.paths import TauPaths
from tau_coding.resources import TauResourcePaths

from ms_tau_sdk.resources.context_compat import install_project_context_filter


def resource_root() -> Path:
    return Path(str(files("ms_tau_sdk.resources")))


class _SkillsDir(Path):
    """A skills directory whose listing also holds the skills inside its group folders.

    Tau reads the folders directly in a skills directory, so the skills in a folder without a
    `SKILL.md`, such as `.tau/skills/<group>/<skill>/SKILL.md`, were skipped. Listing them
    beside the direct entries lets Tau read, index, reload and serve them like any other skill.

    With an Agent's skill selection (ADR 0026, section 3) the listing holds only the selected
    skill folders, whichever group folder holds them.
    """

    selected: ClassVar[frozenset[str] | None] = None

    def iterdir(self) -> Generator[Self]:
        for entry in sorted(Path(self).iterdir()):
            if self._admits(entry):
                yield self.with_segments(entry)
            if _is_group(entry):
                yield from map(self.with_segments, filter(self._admits, _skill_folders(entry)))

    def _admits(self, entry: Path) -> bool:
        if self.selected is None:
            return True
        return entry.name in self.selected and _is_skill(entry)


def _is_skill(folder: Path) -> bool:
    return folder.is_dir() and (folder / "SKILL.md").exists()


def _is_group(folder: Path) -> bool:
    # A symbolic link is not followed into, so a link cannot loop the listing.
    return folder.is_dir() and not folder.is_symlink() and not (folder / "SKILL.md").exists()


def _skill_folders(group: Path) -> Iterator[Path]:
    for entry in sorted(group.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").exists():
            yield entry
        elif _is_group(entry):
            yield from _skill_folders(entry)


class _RepositoryPaths(TauPaths):
    """Tau paths that read the repository as a coding harness: `.tau` and `.agents`.

    This is Tau's own scope, with nested skills. It is selected with
    `TAU_INCLUDE_DEVELOPMENT_RESOURCES` for an Agent that is a coding agent over its own
    repository, whose root `AGENTS.md` and `.agents/` are its instructions (ADR 0025, section 2).
    Tau rebuilds its resource paths on load and reload but keeps this object, so the scope holds
    on both.
    """

    __slots__ = ()

    # Read by the project instruction filter of `context_compat`.
    reads_development_resources: ClassVar[bool] = True
    # Replaced by a class that lists only an Agent's selected skills (ADR 0026, section 3).
    skills_dir_class: ClassVar[type[_SkillsDir]] = _SkillsDir

    def project_skills_dir(self, cwd: Path) -> Path:
        return self.skills_dir_class(super().project_skills_dir(cwd))

    def project_agents_skills_dir(self, cwd: Path) -> Path:
        return self.skills_dir_class(super().project_agents_skills_dir(cwd))


class _TauOnlyPaths(_RepositoryPaths):
    """Tau paths that read the Agent's project resources from `.tau` alone (ADR 0025).

    The project's `.agents/skills`, `.agents/prompts` and `.agents/AGENTS.md` hold instructions
    for the coding agents that develop the repository, not for the Agent it deploys. Every
    `.agents` project location resolves to its `.tau` counterpart here; Tau de-duplicates the
    repeated directories, so each is read once. The `AGENTS.md` files Tau finds without consulting
    these paths are removed by the filter of `context_compat`, which reads the class attribute.
    """

    __slots__ = ()

    reads_development_resources: ClassVar[bool] = False

    def project_agents_dir(self, cwd: Path) -> Path:
        return self.project_tau_dir(cwd)

    def project_agents_skills_dir(self, cwd: Path) -> Path:
        return self.project_skills_dir(cwd)

    def project_agents_prompts_dir(self, cwd: Path) -> Path:
        return self.project_prompts_dir(cwd)


def _with_skill_selection(
    paths_class: type[_RepositoryPaths], skills: frozenset[str]
) -> type[_RepositoryPaths]:
    skills_dir_class = type("_SelectedSkillsDir", (_SkillsDir,), {"selected": skills})
    return type(
        paths_class.__name__,
        (paths_class,),
        {"__slots__": (), "skills_dir_class": skills_dir_class},
    )


def tau_resource_paths(
    cwd: Path,
    *,
    state_home: Path,
    include_development_resources: bool = False,
    skills: frozenset[str] | None = None,
) -> TauResourcePaths:
    """Return SDK defaults plus Tau-native project resource discovery.

    `root` stays on the packaged resources, which are read-only inputs: an
    installed wheel lives in site-packages, so the SDK must never write there.
    `paths` therefore sends every durable Tau write — built-in extension state,
    provider credentials, project trust, diagnostics, sessions — to the
    writable `state_home` instead.

    Project resources come from `.tau` alone unless `include_development_resources`
    also admits the repository's `AGENTS.md` files and `.agents/`. The version-locked
    filter that keeps Tau's `AGENTS.md` discovery to `.tau` is installed here, before
    the session that uses these paths loads; it fails on an unreviewed Tau version.

    `skills`, an Agent's skill selection (ADR 0026), limits every project skill directory
    the scope reads to the named skill folders. `None` lists every project skill.
    """
    install_project_context_filter()
    paths_class: type[_RepositoryPaths] = (
        _RepositoryPaths if include_development_resources else _TauOnlyPaths
    )
    if skills is not None:
        paths_class = _with_skill_selection(paths_class, skills)
    return TauResourcePaths(
        root=resource_root(),
        cwd=cwd,
        agents_root=None,
        paths=paths_class(home=state_home),
    )
