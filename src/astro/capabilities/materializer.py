"""Materialize backend-bound skills into a session-scoped Tau resource root."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath

from astro.backend.client import MainSequenceClient
from astro.backend.models import SessionCapabilityBinding
from astro.errors import ConfigurationError


def _safe_session_key(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "_.-" else "_" for character in value
    )


def _skill_path(value: str) -> Path | None:
    if not value or "\\" in value:
        return None
    source = PurePosixPath(value)
    if source.is_absolute():
        return None
    parts = list(source.parts)
    if parts and parts[0] == "skills":
        parts.pop(0)
    if (
        not parts
        or parts[-1] != "SKILL.md"
        or any(not part or part in {".", ".."} or part.startswith(".") for part in parts)
    ):
        return None
    return Path(*parts)


def _is_repository_capability(binding: SessionCapabilityBinding) -> bool:
    return binding.source_type == "repository" or binding.capability.source_type == "repository"


def _content_digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _prepare_skill_root(asset_root: Path, session_uid: str) -> tuple[Path, Path]:
    agents_root = asset_root.resolve() / _safe_session_key(session_uid) / ".agents"
    skills_root = agents_root / "skills"
    shutil.rmtree(agents_root, ignore_errors=True)
    skills_root.mkdir(parents=True, mode=0o700)
    return agents_root, skills_root


def _resolve_target(skills_root: Path, relative_path: Path) -> Path:
    return (skills_root / relative_path).resolve()


def _write_skill(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    target.chmod(0o600)


def known_capability_hashes(asset_root: Path) -> list[str]:
    cache_root = asset_root.resolve() / ".content"
    if not cache_root.exists():
        return []
    return sorted(path.stem for path in cache_root.glob("*.md") if path.is_file())


async def _materialize_bindings(
    *,
    bindings: list[SessionCapabilityBinding],
    session_uid: str,
    asset_root: Path,
    content_resolver: Callable[[SessionCapabilityBinding], Awaitable[str]],
) -> Path:
    agents_root, skills_root = await asyncio.to_thread(
        _prepare_skill_root,
        asset_root,
        session_uid,
    )
    cache_root = (await asyncio.to_thread(asset_root.resolve)) / ".content"
    await asyncio.to_thread(cache_root.mkdir, parents=True, mode=0o700, exist_ok=True)
    targets: dict[Path, str] = {}
    for binding in bindings:
        capability = binding.capability
        if (
            not binding.is_enabled
            or capability.kind != "skill"
            or _is_repository_capability(binding)
            or not capability.has_content
        ):
            continue
        relative_path = _skill_path(capability.capability_path)
        if relative_path is None:
            raise ConfigurationError(f"Capability {capability.uid} has an unsafe skill path")
        target = await asyncio.to_thread(_resolve_target, skills_root, relative_path)
        if not target.is_relative_to(skills_root):
            raise ConfigurationError(f"Capability {capability.uid} escaped the session skill root")
        expected = capability.content_sha256.removeprefix("sha256:")
        cache_path = cache_root / f"{expected}.md" if expected else None
        if cache_path is not None and cache_path.exists():
            content = await asyncio.to_thread(cache_path.read_text, encoding="utf-8")
        else:
            content = await content_resolver(binding)
        digest = _content_digest(content)
        if expected and expected != digest:
            raise ConfigurationError(f"Capability {capability.uid} content hash does not match")
        if cache_path is not None and not cache_path.exists():
            await asyncio.to_thread(_write_skill, cache_path, content)
        previous = targets.get(target)
        if previous is not None and previous != digest:
            raise ConfigurationError(f"Multiple capabilities resolve to {relative_path}")
        await asyncio.to_thread(_write_skill, target, content)
        targets[target] = digest
    if not targets:
        await asyncio.to_thread(shutil.rmtree, skills_root, ignore_errors=True)
    return agents_root


async def materialize_session_capabilities(
    *,
    backend: MainSequenceClient,
    session_uid: str,
    asset_root: Path,
) -> Path:
    bindings = await backend.list_session_capabilities(session_uid)

    async def resolve(binding: SessionCapabilityBinding) -> str:
        capability = binding.capability
        content = await backend.get_capability_content(capability.uid)
        return content.content

    return await _materialize_bindings(
        bindings=bindings,
        session_uid=session_uid,
        asset_root=asset_root,
        content_resolver=resolve,
    )


async def materialize_bootstrap_capabilities(
    *,
    bindings: list[SessionCapabilityBinding],
    session_uid: str,
    asset_root: Path,
) -> Path:
    async def resolve(binding: SessionCapabilityBinding) -> str:
        content = binding.capability.content
        if content is None:
            raise ConfigurationError(
                f"Capability {binding.capability.uid} body is not cached or bootstrapped"
            )
        return content

    return await _materialize_bindings(
        bindings=bindings,
        session_uid=session_uid,
        asset_root=asset_root,
        content_resolver=resolve,
    )
