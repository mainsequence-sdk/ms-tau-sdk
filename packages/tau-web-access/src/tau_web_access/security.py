"""URL validation for server-side content fetching."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """Raised when a URL could address a private or local network resource."""


def _is_unsafe(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


async def validate_public_url(url: str, *, allow_private_networks: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("Only http and https URLs are supported")
    if not parsed.hostname:
        raise UnsafeUrlError("URL must include a hostname")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("Credentials in URLs are not allowed")
    if allow_private_networks:
        return

    try:
        literal_ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if _is_unsafe(str(literal_ip)):
            raise UnsafeUrlError(f"Private or local address is not allowed: {parsed.hostname}")
        return

    loop = asyncio.get_running_loop()
    records: Iterable[
        tuple[object, object, object, object, tuple[object, ...]]
    ] = await loop.getaddrinfo(
        parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )
    addresses = {str(record[4][0]) for record in records}
    if not addresses:
        raise UnsafeUrlError(f"Hostname did not resolve: {parsed.hostname}")
    if any(_is_unsafe(address) for address in addresses):
        raise UnsafeUrlError(f"Hostname resolves to a private or local address: {parsed.hostname}")


def resolve_local_path(value: str, *, cwd: Path, allow_local_files: bool) -> Path | None:
    parsed = urlparse(value)
    if parsed.scheme == "file":
        if not allow_local_files:
            raise UnsafeUrlError("Local file access is disabled")
        path = Path(parsed.path)
    elif parsed.scheme:
        return None
    else:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        if not candidate.exists():
            return None
        if not allow_local_files:
            raise UnsafeUrlError("Local file access is disabled")
        path = candidate

    resolved = (cwd / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        resolved.relative_to(cwd.resolve())
    except ValueError as error:
        raise UnsafeUrlError(
            "Local files must be inside the configured working directory"
        ) from error
    return resolved


__all__ = ["UnsafeUrlError", "resolve_local_path", "validate_public_url"]
