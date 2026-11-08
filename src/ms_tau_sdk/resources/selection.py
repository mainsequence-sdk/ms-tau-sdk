"""The per-Agent selection of `.tau` resources from session bootstrap (ADR 0026).

A repository may deploy several Agents from one branch. The platform resolves each Agent's
`harness_agent.spec.runtime_resources` and returns it at session bootstrap. This module reads that
selection strictly, resolves it inside the workspace before Tau runs any project code, and checks
that the session Tau composed holds exactly what was selected, on load and after every reload.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from tau_agent.types import JSONValue
from tau_coding import CodingSession
from tau_coding.extensions import discover_extensions
from tau_coding.resources import TauResourcePaths

from ms_tau_sdk.errors import ConfigurationError

RUNTIME_RESOURCES_SCHEMA_VERSION = 1
# `.tau/` folders whose files are other resource kinds, never an Agent's system prompt.
_RESERVED_TAU_FOLDERS = frozenset({"skills", "prompts", "extensions"})
_EXTENSION_ROOT = PurePosixPath(".tau/extensions")


def _single_segment(value: str, *, kind: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"{kind} {value!r} must be one folder or file name")
    return value


def _unique(values: tuple[str, ...], *, kind: str) -> tuple[str, ...]:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(f"{kind} are listed more than once: {', '.join(duplicates)}")
    return values


class RuntimeResourceSelection(BaseModel):
    """The resources one Agent's sessions load from `.tau/`; an omitted field selects all."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    system_prompt: str | None = None
    skills: tuple[str, ...] | None = None
    extensions: tuple[str, ...] | None = None

    @field_validator("skills", "extensions", mode="before")
    @classmethod
    def _list_to_tuple(cls, value: object) -> object:
        # JSON carries lists; strict mode would otherwise reject them for a tuple.
        return tuple(value) if isinstance(value, list) else value

    @field_validator("system_prompt")
    @classmethod
    def _validate_system_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or "\x00" in value
            or path.is_absolute()
            or any(part in {".", ".."} for part in value.split("/"))
            or path.as_posix() != value
        ):
            raise ValueError(f"system_prompt {value!r} must be a plain workspace-relative path")
        if len(path.parts) < 2 or path.parts[0] != ".tau":
            raise ValueError(f"system_prompt {value!r} must be a file under .tau/")
        if len(path.parts) > 2 and path.parts[1] in _RESERVED_TAU_FOLDERS:
            raise ValueError(
                f"system_prompt {value!r} must be outside .tau/skills, .tau/prompts "
                "and .tau/extensions"
            )
        if path.suffix.lower() != ".md":
            raise ValueError(f"system_prompt {value!r} must be a Markdown (.md) file")
        return value

    @field_validator("skills")
    @classmethod
    def _validate_skills(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        for name in value:
            _single_segment(name, kind="Skill name")
        return _unique(value, kind="Skills")

    @field_validator("extensions")
    @classmethod
    def _validate_extensions(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        for entry in value:
            _single_segment(entry, kind="Extension entry")
            if entry.startswith(("_", ".")):
                # Tau never discovers these as project extensions.
                raise ValueError(f"Extension entry {entry!r} must not start with '_' or '.'")
        return _unique(value, kind="Extensions")


def parse_runtime_resources(raw: object) -> RuntimeResourceSelection | None:
    """Read the bootstrap's `runtime_resources`; absent or null keeps the whole `.tau/`."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ConfigurationError("Session bootstrap runtime_resources must be an object")
    fields: dict[str, Any] = {key: value for key, value in raw.items() if value is not None}
    try:
        return RuntimeResourceSelection.model_validate(fields)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or 'runtime_resources'}: {issue['msg']}"
            for issue in error.errors(include_url=False, include_input=False)
        )
        raise ConfigurationError(
            f"Session bootstrap runtime_resources is invalid: {problems}"
        ) from None


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeResources:
    """A selection resolved inside one workspace, ready for Tau's session configuration."""

    selection: RuntimeResourceSelection
    cwd: Path
    custom_system_prompt: str | None
    extension_paths: tuple[Path, ...]

    @property
    def skill_names(self) -> frozenset[str] | None:
        skills = self.selection.skills
        return None if skills is None else frozenset(skills)

    @property
    def skills_enabled(self) -> bool:
        return self.selection.skills != ()

    @property
    def project_extensions_enabled(self) -> bool:
        return self.selection.extensions is None

    def summary(self) -> dict[str, JSONValue]:
        """The selection for logs and inspection, with paths relative to the workspace."""
        skills: list[JSONValue] | None = (
            None if self.selection.skills is None else [*self.selection.skills]
        )
        extensions: list[JSONValue] | None = (
            None
            if self.selection.extensions is None
            else [(_EXTENSION_ROOT / entry).as_posix() for entry in self.selection.extensions]
        )
        return {
            "systemPrompt": self.selection.system_prompt,
            "skills": skills,
            "extensions": extensions,
        }

    def verify(self, session: CodingSession) -> None:
        """Fail unless Tau composed exactly the selected skills and extensions."""
        selected_skills = self.skill_names
        if selected_skills is not None:
            loaded = {skill.name for skill in session.skills}
            missing = selected_skills - loaded
            if missing:
                raise ConfigurationError(
                    "Selected skills did not load: " + ", ".join(sorted(missing))
                )
            unexpected = loaded - selected_skills
            if unexpected:
                raise ConfigurationError(
                    "Skills outside the selection loaded: " + ", ".join(sorted(unexpected))
                )
        if self.selection.extensions is None:
            return
        entries = dict(zip(self.selection.extensions, self.extension_paths, strict=True))
        loaded_paths = [
            Path(item.path).resolve()
            for item in session.extension_runtime.extension_metadata
            if item.source in {"explicit", "project"} and item.path is not None
        ]
        missing_entries = [
            name
            for name, entry in entries.items()
            if not any(_within(path, entry) for path in loaded_paths)
        ]
        if missing_entries:
            raise ConfigurationError(
                "Selected extensions did not load: " + ", ".join(sorted(missing_entries))
            )
        # A folder may hold several extension entries; one that failed must not go unnoticed.
        failed_entries = {
            name
            for diagnostic in session.extension_runtime.diagnostics
            if diagnostic.severity == "error" and diagnostic.path is not None
            for name, entry in entries.items()
            if _within(Path(diagnostic.path).resolve(), entry)
        }
        if failed_entries:
            raise ConfigurationError(
                "Selected extensions failed to load: " + ", ".join(sorted(failed_entries))
            )
        if any(
            not any(_within(path, entry) for entry in entries.values()) for path in loaded_paths
        ):
            raise ConfigurationError("Extensions outside the selection loaded")


def _within(path: Path, entry: Path) -> bool:
    return path == entry or entry in path.parents


def _no_symbolic_links(path: Path, *, below: Path) -> bool:
    current = path
    while current != below:
        if current.is_symlink():
            return False
        if current.parent == current:
            return False
        current = current.parent
    return True


def _read_system_prompt(relative: str, *, cwd: Path) -> str:
    workspace = cwd.resolve()
    path = workspace / relative
    try:
        if not _no_symbolic_links(path, below=workspace):
            raise ConfigurationError(
                f"Selected system prompt {relative} must not be a symbolic link"
            )
        resolved = path.resolve(strict=True)
        resolved.relative_to(workspace / ".tau")
        if not resolved.is_file():
            raise ConfigurationError(f"Selected system prompt {relative} is not a file")
        raw = resolved.read_bytes()
    except FileNotFoundError:
        raise ConfigurationError(f"Selected system prompt {relative} does not exist") from None
    except ValueError:
        raise ConfigurationError(f"Selected system prompt {relative} is outside .tau/") from None
    except OSError as error:
        raise ConfigurationError(
            f"Selected system prompt {relative} cannot be read: {error.strerror}"
        ) from None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigurationError(f"Selected system prompt {relative} is not UTF-8") from None


def _resolve_extensions(entries: Iterable[str], *, cwd: Path) -> tuple[Path, ...]:
    entries = tuple(entries)
    if not entries:
        return ()
    workspace = cwd.resolve()
    root = workspace / ".tau" / "extensions"
    if not _no_symbolic_links(root, below=workspace) or not root.is_dir():
        raise ConfigurationError(
            ".tau/extensions must be a folder in the workspace to select extensions"
        )
    # The extensions Tau discovers as project extensions; a selection only narrows them.
    discovered, _ = discover_extensions(
        TauResourcePaths(root=root, cwd=workspace),
        include_project_dir=True,
        include_user_dir=False,
    )
    discovered_paths = [entry.path.resolve() for entry in discovered]
    resolved: list[Path] = []
    for name in entries:
        entry = root / name
        relative = (_EXTENSION_ROOT / name).as_posix()
        if entry.is_symlink():
            raise ConfigurationError(f"Selected extension {relative} must not be a symbolic link")
        if not entry.exists():
            raise ConfigurationError(f"Selected extension {relative} does not exist")
        if not any(_within(path, entry) for path in discovered_paths):
            raise ConfigurationError(
                f"Selected extension {relative} is not an extension Tau discovers in "
                ".tau/extensions"
            )
        resolved.append(entry)
    return tuple(resolved)


def resolve_runtime_resources(
    selection: RuntimeResourceSelection, *, cwd: Path
) -> ResolvedRuntimeResources:
    """Read the selected prompt and resolve the selected extensions before Tau loads."""
    custom_system_prompt = (
        None
        if selection.system_prompt is None
        else _read_system_prompt(selection.system_prompt, cwd=cwd)
    )
    extension_paths = (
        () if selection.extensions is None else _resolve_extensions(selection.extensions, cwd=cwd)
    )
    return ResolvedRuntimeResources(
        selection=selection,
        cwd=cwd,
        custom_system_prompt=custom_system_prompt,
        extension_paths=extension_paths,
    )
