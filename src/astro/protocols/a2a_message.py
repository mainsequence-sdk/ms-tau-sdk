"""Side-effect-separated A2A Message/Part normalization shared by runtime adapters."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException

from astro.protocols.a2a_roles import (
    A2AMessageDirection,
    has_a2a_direction,
)
from astro.protocols.strict_json import StrictJsonContract, build_strict_json_contract
from astro.settings import Settings

OUTPUT_CONTRACT_EXTENSION_URI = "https://mainsequence.ai/a2a/extensions/output-contract/v1"
SESSION_IDENTITY_KEYS = frozenset(
    {
        "agent_session_uid",
        "agentSessionUid",
        "session_uid",
        "sessionUid",
        "thread_id",
        "threadId",
    }
)
AGENT_IDENTITY_KEYS = frozenset({"agent_uid", "agentUid"})
IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
TEXT_MEDIA_TYPES = frozenset({"text/plain", "text/markdown", "application/json", "text/csv"})


@dataclass(frozen=True, slots=True)
class PreparedFilePart:
    index: int
    path: Path
    filename: str
    media_type: str
    size: int
    sha256: str


@dataclass(slots=True)
class PreparedA2AInput:
    message: dict[str, Any]
    text: str
    data_parts: list[Any]
    files: list[PreparedFilePart]
    cleanup_root: Path | None = None

    def prompt(self, *, include_file_manifest: bool = True) -> str:
        sections = [self.text] if self.text else []
        for index, data in enumerate(self.data_parts):
            sections.append(
                f"Data part {index + 1}:\n"
                + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            )
        if self.files and include_file_manifest:
            manifest = "\n".join(
                f"- {item.path} ({item.media_type}, {item.size} bytes)" for item in self.files
            )
            sections.append(f"Attached files:\n{manifest}")
        return "\n\n".join(section for section in sections if section).strip()

    def cleanup(self) -> None:
        if self.cleanup_root is not None:
            shutil.rmtree(self.cleanup_root, ignore_errors=True)
            self.cleanup_root = None


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "item"


def _contains_key(value: object, keys: frozenset[str]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key in keys:
                return key
            found = _contains_key(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _contains_key(item, keys)
            if found is not None:
                return found
    return None


def _validate_signature(raw: bytes, *, media_type: str, index: int) -> None:
    valid = True
    if media_type == "application/pdf":
        valid = raw.startswith(b"%PDF-")
    elif media_type == "image/png":
        valid = raw.startswith(b"\x89PNG\r\n\x1a\n")
    elif media_type == "image/jpeg":
        valid = raw.startswith(b"\xff\xd8\xff")
    elif media_type == "image/webp":
        valid = len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
    elif media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        valid = raw.startswith(b"PK\x03\x04")
    elif media_type in TEXT_MEDIA_TYPES:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            valid = False
    if not valid:
        raise HTTPException(
            status_code=400,
            detail=f"message.parts[{index}].raw does not match mediaType {media_type}",
        )


def _file_directory(
    *,
    config: Settings,
    context_id: str | None,
    message_id: str,
    request_scoped: bool,
) -> tuple[Path, Path | None]:
    if request_scoped:
        config.sessionless_asset_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root = Path(tempfile.mkdtemp(prefix="response-", dir=config.sessionless_asset_root))
        os.chmod(root, 0o700)
        return root, root
    directory = (
        config.a2a_asset_root
        / _safe_component(context_id or "context")
        / "a2a-inputs"
        / _safe_component(message_id)
    )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory, None


def prepare_a2a_input(
    body: dict[str, Any],
    config: Settings,
    *,
    context_policy: Literal["required", "forbidden"],
    allowed_media_types: set[str] | frozenset[str],
) -> PreparedA2AInput:
    """Validate one canonical A2A message and materialize accepted raw parts."""

    if context_policy == "forbidden":
        forbidden = _contains_key(
            body,
            SESSION_IDENTITY_KEYS | AGENT_IDENTITY_KEYS,
        )
        if forbidden is not None:
            raise HTTPException(status_code=400, detail=f"{forbidden} is not allowed")
    message = body.get("message")
    if not isinstance(message, dict):
        raise HTTPException(status_code=400, detail="message must be an object")
    if "kind" in message:
        raise HTTPException(
            status_code=400,
            detail="message.kind is not part of the A2A v1 Message envelope",
        )
    if not has_a2a_direction(message, A2AMessageDirection.REQUESTER):
        wire_role = A2AMessageDirection.REQUESTER.value
        raise HTTPException(
            status_code=400,
            detail=(f"message.role must identify the requester (wire value {wire_role})"),
        )
    message_id = str(message.get("messageId") or "").strip()
    if not message_id:
        raise HTTPException(status_code=400, detail="message.messageId is required")
    context_id = str(message.get("contextId") or "").strip()
    if context_policy == "required" and not context_id:
        raise HTTPException(status_code=400, detail="message.contextId is required")
    if context_policy == "forbidden" and (context_id or message.get("taskId")):
        raise HTTPException(
            status_code=400,
            detail="message.contextId and message.taskId are not allowed",
        )
    if context_policy == "forbidden" and body.get("taskId"):
        raise HTTPException(status_code=400, detail="taskId is not allowed")
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        raise HTTPException(status_code=400, detail="message.parts must be a non-empty array")

    configuration = body.get("configuration", {})
    if not isinstance(configuration, dict):
        raise HTTPException(status_code=400, detail="configuration must be an object")
    if context_policy == "forbidden":
        unsupported_configuration = {
            "historyLength",
            "pushNotificationConfig",
            "blocking",
            "returnImmediately",
        }.intersection(configuration)
        if unsupported_configuration:
            field = sorted(unsupported_configuration)[0]
            raise HTTPException(status_code=400, detail=f"configuration.{field} is not allowed")
        if configuration.get("responseKind", "message") != "message":
            raise HTTPException(
                status_code=400,
                detail="configuration.responseKind must be message",
            )

    directory: Path | None = None
    cleanup_root: Path | None = None
    text_parts: list[str] = []
    data_parts: list[Any] = []
    files: list[PreparedFilePart] = []
    total_bytes = 0
    normalized_allowed = {item.lower() for item in allowed_media_types}
    try:
        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"message.parts[{index}] must be an object",
                )
            if "kind" in part:
                raise HTTPException(
                    status_code=400,
                    detail=(f"message.parts[{index}].kind is not part of the A2A v1 Part envelope"),
                )
            variants = [key for key in ("text", "data", "raw", "url") if key in part]
            if len(variants) != 1:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"message.parts[{index}] must contain exactly one of text, data, raw, url"
                    ),
                )
            variant = variants[0]
            if variant == "url":
                raise HTTPException(
                    status_code=400,
                    detail=f"message.parts[{index}].url is not supported",
                )
            if variant == "text":
                value = part["text"]
                if not isinstance(value, str) or not value.strip():
                    raise HTTPException(
                        status_code=400,
                        detail=f"message.parts[{index}].text must be non-empty",
                    )
                text_parts.append(value.strip())
                continue
            if variant == "data":
                data_parts.append(part["data"])
                continue

            media_type = str(part.get("mediaType") or "").strip().lower()
            if media_type not in normalized_allowed:
                raise HTTPException(
                    status_code=415,
                    detail=(
                        f"message.parts[{index}].mediaType "
                        f"{media_type or '<missing>'} is unsupported"
                    ),
                )
            filename = str(part.get("filename") or "").strip()
            if not filename or Path(filename).name != filename:
                label = "safe PDF filename" if media_type == "application/pdf" else "safe filename"
                raise HTTPException(
                    status_code=400,
                    detail=f"message.parts[{index}].filename must be a {label}",
                )
            try:
                raw = base64.b64decode(str(part["raw"]), validate=True)
            except (ValueError, binascii.Error) as error:
                raise HTTPException(
                    status_code=400,
                    detail=f"message.parts[{index}].raw is not valid base64",
                ) from error
            if not raw:
                raise HTTPException(
                    status_code=400,
                    detail=f"message.parts[{index}].raw must not be empty",
                )
            if len(raw) > config.a2a_max_inline_file_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"message.parts[{index}].raw exceeds the inline file limit",
                )
            total_bytes += len(raw)
            if total_bytes > config.a2a_max_aggregate_file_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="inline files exceed the aggregate limit",
                )
            if len(files) >= config.a2a_max_inline_file_count:
                raise HTTPException(status_code=413, detail="too many inline files")
            _validate_signature(raw, media_type=media_type, index=index)
            if directory is None:
                directory, cleanup_root = _file_directory(
                    config=config,
                    context_id=context_id or None,
                    message_id=message_id,
                    request_scoped=context_policy == "forbidden",
                )
            digest = hashlib.sha256(raw).hexdigest()
            path = directory / f"{index}-{digest[:12]}-{_safe_component(filename)}"
            path.write_bytes(raw)
            os.chmod(path, 0o600)
            files.append(
                PreparedFilePart(
                    index=index,
                    path=path,
                    filename=filename,
                    media_type=media_type,
                    size=len(raw),
                    sha256=digest,
                )
            )
        if not text_parts and not data_parts and not files:
            raise HTTPException(status_code=400, detail="message.parts has no usable content")
        return PreparedA2AInput(
            message=message,
            text="\n".join(text_parts).strip(),
            data_parts=data_parts,
            files=files,
            cleanup_root=cleanup_root,
        )
    except BaseException:
        if cleanup_root is not None:
            shutil.rmtree(cleanup_root, ignore_errors=True)
        raise


def output_contract(body: dict[str, Any]) -> StrictJsonContract:
    configuration = body.get("configuration", {})
    modes = configuration.get("acceptedOutputModes", []) if isinstance(configuration, dict) else []
    metadata = body.get("metadata", {})
    extension = (
        metadata.get(OUTPUT_CONTRACT_EXTENSION_URI, {}) if isinstance(metadata, dict) else {}
    )
    strict = bool(extension.get("strict")) if isinstance(extension, dict) else False
    response_format = body.get("responseFormat", body.get("response_format"))
    repair: object = body.get("jsonRepair", body.get("json_repair", {}))
    if isinstance(extension, dict):
        response_format = extension.get(
            "responseFormat",
            extension.get("response_format", response_format),
        )
        repair = extension.get("jsonRepair", extension.get("json_repair", repair))
        if "jsonRepairAttempts" in extension:
            repair = {"attempts": extension["jsonRepairAttempts"]}
    attempts_value = repair.get("attempts", 3) if isinstance(repair, dict) else repair
    try:
        attempts = int(attempts_value) if isinstance(attempts_value, int | str) else 3
    except (TypeError, ValueError):
        attempts = 3
    return build_strict_json_contract(
        response_format,
        force_strict=strict or "application/json" in modes,
        repair_attempts=attempts,
    )


def agent_message(
    *,
    text: str,
    strict_json: bool,
    context_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if strict_json:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=502,
                detail=f"Agent did not return strict JSON: {error}",
            ) from error
        parts = [{"data": data, "mediaType": "application/json"}]
    else:
        parts = [{"text": text}]
    message: dict[str, Any] = {
        "messageId": f"msg-agent-{uuid.uuid4()}",
        "role": A2AMessageDirection.RESPONDER.value,
        "parts": parts,
    }
    if context_id is not None:
        message["contextId"] = context_id
    if metadata:
        message["metadata"] = metadata
    return message


def sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()
