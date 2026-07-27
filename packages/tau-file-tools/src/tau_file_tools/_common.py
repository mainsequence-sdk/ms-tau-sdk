"""Shared implementation details that are not part of the public package API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from tau_agent.tools import ToolCancellationToken
from tau_agent.types import JSONValue

DEFAULT_MAX_OUTPUT_BYTES = 50 * 1024


class ToolInputError(ValueError):
    """Raised when tool arguments are invalid."""


def root_path(cwd: str | Path | None) -> Path:
    return (Path.cwd() if cwd is None else Path(cwd)).resolve()


def resolve_path(root: Path, value: str | None) -> Path:
    path = root if not value else Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def string_arg(arguments: Mapping[str, JSONValue], name: str, *, required: bool) -> str | None:
    value = arguments.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value):
        raise ToolInputError(f"{name} must be a non-empty string")
    return value


def positive_int_arg(
    arguments: Mapping[str, JSONValue],
    name: str,
    *,
    default: int,
) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ToolInputError(f"{name} must be a positive integer")
    return value


def nonnegative_int_arg(
    arguments: Mapping[str, JSONValue],
    name: str,
    *,
    default: int,
) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ToolInputError(f"{name} must be a non-negative integer")
    return value


def bool_arg(arguments: Mapping[str, JSONValue], name: str) -> bool:
    value = arguments.get(name, False)
    if not isinstance(value, bool):
        raise ToolInputError(f"{name} must be a boolean")
    return value


def check_cancelled(signal: ToolCancellationToken | None) -> None:
    if signal is not None and signal.is_cancelled():
        raise RuntimeError("Operation aborted")


async def communicate_with_cancellation(
    process: asyncio.subprocess.Process,
    signal: ToolCancellationToken | None,
) -> tuple[bytes, bytes]:
    communication = asyncio.create_task(process.communicate())
    try:
        while not communication.done():
            check_cancelled(signal)
            await asyncio.wait((communication,), timeout=0.05)
        return communication.result()
    except BaseException:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(communication, timeout=1)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await communication
        raise


def truncate_bytes(value: str, max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True
