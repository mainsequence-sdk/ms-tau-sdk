"""Small, explicit configuration for the standalone board."""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def local_url(value: str) -> str:
    """Accept HTTP on an explicit loopback host, without URL credentials or paths."""
    candidate = value.strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme != "http" or parsed.username or parsed.password:
        raise ValueError("Tau URL must use HTTP on loopback without credentials")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Tau URL must contain only a scheme, host, and port")
    host = parsed.hostname or ""
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("Tau URL must use a loopback host")
        except ValueError as error:
            if "loopback host" in str(error):
                raise
            raise ValueError("Tau URL must use a loopback host") from error
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Tau URL has an invalid port") from error
    if port is None or not 1 <= port <= 65535:
        raise ValueError("Tau URL must include a port")
    return candidate


def state_directory(value: str | Path | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("State directory must be an absolute path")
    return path.resolve()


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    url: str
    provider: str = ""
    model: str = ""
    thinking: str = ""
    state_dir: Path | None = None

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Profile:
        allowed = {"name", "url", "provider", "model", "thinking", "stateDir"}
        if set(value) - allowed:
            raise ValueError("Profile contains unknown fields")
        name = str(value.get("name") or "").strip()
        if not name or len(name) > 64:
            raise ValueError("Profile name must be 1-64 characters")
        raw_state_dir = value.get("stateDir")
        if raw_state_dir is not None and not isinstance(raw_state_dir, str):
            raise ValueError("Profile stateDir must be a path string")
        return cls(
            name=name,
            url=local_url(str(value.get("url") or "")),
            provider=str(value.get("provider") or "").strip()[:128],
            model=str(value.get("model") or "").strip()[:128],
            thinking=str(value.get("thinking") or "").strip()[:32],
            state_dir=state_directory(raw_state_dir),
        )

    def public(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "url": self.url,
            "provider": self.provider,
            "model": self.model,
            "thinking": self.thinking,
            "stateDir": str(self.state_dir) if self.state_dir else None,
        }


@dataclass(frozen=True, slots=True)
class BoardSettings:
    profiles: tuple[Profile, ...]
    state_root: Path
    state_dir: Path | None
    port: int = 8788

    @classmethod
    def from_environment(
        cls,
        *,
        tau_url: str | None = None,
        state_dir: str | None = None,
        port: int | None = None,
    ) -> BoardSettings:
        initial_url = tau_url or os.getenv("TAU_BOARD_TAU_URL")
        if not initial_url:
            tau_port = int(os.getenv("MAINSEQUENCE_TAU_PORT", "8787"))
            initial_url = f"http://127.0.0.1:{tau_port}"
        default = Profile(
            name="Local Tau",
            url=local_url(initial_url),
            provider=os.getenv("TAU_LOCAL_PROVIDER", "").strip(),
            model=os.getenv("TAU_LOCAL_MODEL", "").strip(),
            thinking=os.getenv("TAU_LOCAL_THINKING", "").strip(),
        )
        extra_raw = os.getenv("TAU_BOARD_PROFILES", "[]")
        try:
            extra = json.loads(extra_raw)
        except json.JSONDecodeError as error:
            raise ValueError("TAU_BOARD_PROFILES must be a JSON array") from error
        if not isinstance(extra, list):
            raise ValueError("TAU_BOARD_PROFILES must be a JSON array")
        profiles = (default, *(Profile.from_dict(item) for item in extra if isinstance(item, dict)))
        if len(profiles) != len(extra) + 1 or len(profiles) > 16:
            raise ValueError("TAU_BOARD_PROFILES must contain at most 15 profile objects")
        if len({profile.name for profile in profiles}) != len(profiles):
            raise ValueError("Profile names must be unique")
        selected_port = port or int(os.getenv("TAU_BOARD_PORT", "8788"))
        if not 1 <= selected_port <= 65535:
            raise ValueError("Board port must be between 1 and 65535")
        state_root = Path(os.getenv("TAU_LOCAL_STATE_ROOT", "~/.tau/mainsequence"))
        return cls(
            profiles=profiles,
            state_root=state_root.expanduser().resolve(),
            state_dir=state_directory(state_dir or os.getenv("TAU_BOARD_STATE_DIR")),
            port=selected_port,
        )
