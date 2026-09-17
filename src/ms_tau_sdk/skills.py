"""Explicit installation of version-matched Main Sequence TAU SDK skills."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path

SKILL_NAMESPACE = "ms_tau_sdk"
PIN_FILE_NAME = "PINNED_FROM.txt"


class SkillSyncError(RuntimeError):
    """Raised when the managed skill namespace cannot be synchronized safely."""


@dataclass(frozen=True)
class SkillSyncResult:
    """Serializable result for a skill synchronization request."""

    destination: str
    dry_run: bool
    installed_version: str
    skills: tuple[str, ...]

    def to_json(self) -> str:
        """Return a stable machine-readable representation."""
        return json.dumps(asdict(self), sort_keys=True)


def packaged_skills_path() -> Path:
    """Return the immutable skills bundled with the installed SDK."""
    path = Path(__file__).resolve().parent / "agent_skills"
    if not path.is_dir():
        raise SkillSyncError(f"Installed ms-tau-sdk is missing its skill bundle: {path}")
    return path


def list_packaged_skills() -> tuple[str, ...]:
    """List valid immediate skill directories in the installed bundle."""
    root = packaged_skills_path()
    return tuple(
        candidate.name
        for candidate in sorted(root.iterdir(), key=lambda item: item.name)
        if candidate.is_dir() and (candidate / "SKILL.md").is_file()
    )


def resolve_packaged_skill(name: str) -> Path:
    """Resolve one bundled skill without allowing path traversal."""
    if name not in list_packaged_skills():
        available = ", ".join(list_packaged_skills()) or "none"
        raise SkillSyncError(f"Unknown ms-tau-sdk skill {name!r}; available skills: {available}")
    return packaged_skills_path() / name / "SKILL.md"


def managed_skills_path(project_path: Path) -> Path:
    """Return the SDK-owned destination under a consuming repository."""
    return project_path.expanduser().resolve() / ".agents" / "skills" / SKILL_NAMESPACE


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _pin_contents(skills: tuple[str, ...]) -> str:
    installed_version = version("ms-tau-sdk")
    return "\n".join(
        (
            "schema=1",
            "library_name=ms-tau-sdk",
            f"namespace={SKILL_NAMESPACE}",
            f"pinned_version={installed_version}",
            "skills_path=src/ms_tau_sdk/agent_skills",
            f"skills={','.join(skills)}",
            "command=ms-tau skills sync --path .",
            "",
        )
    )


def sync_skills(project_path: Path, *, dry_run: bool = False) -> SkillSyncResult:
    """Atomically replace only the SDK-owned managed skill namespace."""
    project = project_path.expanduser().resolve()
    if not project.is_dir():
        raise SkillSyncError(f"Project path is not an existing directory: {project}")

    source = packaged_skills_path().resolve()
    destination = managed_skills_path(project)
    if _paths_overlap(source, destination):
        raise SkillSyncError("Refusing to copy the packaged skill source into itself")
    if destination.is_symlink():
        raise SkillSyncError(
            f"Managed skill destination must not be a symbolic link: {destination}"
        )
    if destination.exists() and not destination.is_dir():
        raise SkillSyncError(f"Managed skill destination is not a directory: {destination}")

    skills = list_packaged_skills()
    result = SkillSyncResult(
        destination=str(destination),
        dry_run=dry_run,
        installed_version=version("ms-tau-sdk"),
        skills=skills,
    )
    if dry_run:
        return result

    destination.parent.mkdir(parents=True, exist_ok=True)
    operation_id = uuid.uuid4().hex
    staging = destination.parent / f".{SKILL_NAMESPACE}.{operation_id}.tmp"
    backup = destination.parent / f".{SKILL_NAMESPACE}.{operation_id}.bak"
    try:
        shutil.copytree(source, staging)
        (staging / PIN_FILE_NAME).write_text(_pin_contents(skills), encoding="utf-8")
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(staging, destination)
        except BaseException:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists() and destination.exists():
            shutil.rmtree(backup)

    return result
