"""Conservative edits to named, non-secret settings in a local dotenv file."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .config import SECRET_ENVIRONMENT, VISIBLE_ENVIRONMENT, local_url, state_directory

MAX_ENV_FILE_BYTES = 64 * 1024
EDITABLE_NAMES = frozenset(row[1] for row in VISIBLE_ENVIRONMENT)
ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=(.*?)(?:\r?\n)?$")
PORT_NAMES = {"MAINSEQUENCE_TAU_PORT", "TAU_BOARD_PORT"}
BOOLEAN_NAMES = {"TAU_LOCAL_MODE"}


class EnvFileError(ValueError):
    """A local dotenv file cannot be read or changed safely."""


def _read(path: Path) -> str:
    if path.is_symlink():
        raise EnvFileError("The selected .env file must not be a symlink")
    if not path.exists():
        return ""
    if not path.is_file() or path.stat().st_size > MAX_ENV_FILE_BYTES:
        raise EnvFileError("The selected .env file is not a regular file under 64 KiB")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise EnvFileError("Cannot read the selected .env file") from error


def _value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            decoded = json.loads(value)
            if isinstance(decoded, str):
                return decoded
        except json.JSONDecodeError:
            pass
    if len(value) >= 2 and value[0] in {'"', "'"} and value[-1] == value[0]:
        return value[1:-1]
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def read_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _read(path).splitlines():
        match = ASSIGNMENT.match(line)
        if match and match.group(1) in EDITABLE_NAMES:
            values[match.group(1)] = _value(match.group(2))
    return values


def credential_presence(path: Path) -> dict[str, bool]:
    present = {name: False for name in SECRET_ENVIRONMENT}
    for line in _read(path).splitlines():
        match = ASSIGNMENT.match(line)
        if match and match.group(1) in present:
            present[match.group(1)] = bool(_value(match.group(2)))
    return present


def _validate(name: str, value: str) -> str:
    if name not in EDITABLE_NAMES:
        raise EnvFileError(f"{name} cannot be edited in Tau Board")
    if len(value) > 512 or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise EnvFileError(f"{name} must be one line of at most 512 characters")
    value = value.strip()
    if not value:
        return ""
    if name in PORT_NAMES and (not value.isdecimal() or not 1 <= int(value) <= 65535):
        raise EnvFileError(f"{name} must be a port from 1 to 65535")
    if name in BOOLEAN_NAMES and value.lower() not in {"true", "false"}:
        raise EnvFileError(f"{name} must be true or false")
    if name == "MAINSEQUENCE_AUTH_MODE" and value not in {"jwt", "runtime_credential"}:
        raise EnvFileError("MAINSEQUENCE_AUTH_MODE must be jwt or runtime_credential")
    if name == "TAU_BOARD_TAU_URL":
        local_url(value)
    if name == "TAU_BOARD_STATE_DIR":
        state_directory(value)
    if name == "MAINSEQUENCE_ENDPOINT":
        try:
            parsed = urlsplit(value)
        except ValueError as error:
            raise EnvFileError("MAINSEQUENCE_ENDPOINT is not a valid HTTP URL") from error
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise EnvFileError(
                "MAINSEQUENCE_ENDPOINT must be an HTTP URL without credentials or query"
            )
        try:
            port = parsed.port
        except ValueError as error:
            raise EnvFileError("MAINSEQUENCE_ENDPOINT has an invalid port") from error
        if port is not None and not 1 <= port <= 65535:
            raise EnvFileError("MAINSEQUENCE_ENDPOINT has an invalid port")
    return value


def write_values(path: Path, changes: dict[str, str]) -> dict[str, str]:
    if not changes or len(changes) > len(EDITABLE_NAMES):
        raise EnvFileError("Choose one or more settings to save")
    validated = {name: _validate(name, value) for name, value in changes.items()}
    existing = _read(path)
    if not path.parent.is_dir():
        raise EnvFileError("The .env parent directory does not exist")
    newline = "\r\n" if "\r\n" in existing else "\n"
    lines: list[str] = []
    seen: set[str] = set()
    for line in existing.splitlines(keepends=True):
        match = ASSIGNMENT.match(line)
        name = match.group(1) if match else ""
        if name not in validated:
            lines.append(line)
        elif name not in seen and validated[name]:
            lines.append(f"{name}={json.dumps(validated[name], ensure_ascii=False)}{newline}")
            seen.add(name)
    for name, value in validated.items():
        if value and name not in seen:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            lines.append(f"{name}={json.dumps(value, ensure_ascii=False)}{newline}")
    output = "".join(lines)
    if len(output.encode("utf-8")) > MAX_ENV_FILE_BYTES:
        raise EnvFileError("The edited .env file would exceed 64 KiB")
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".tau-board-env-", delete=False
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.chmod(mode)
        os.replace(temp_path, path)
    except OSError as error:
        raise EnvFileError("Cannot save the selected .env file") from error
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return read_values(path)
