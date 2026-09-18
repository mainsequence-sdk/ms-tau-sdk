"""Deterministic, credential-free Tau resume snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.metadata import version

from tau_agent.session import SessionEntry

from ms_tau_sdk.backend.models import (
    SessionEntryList,
    TauResumeSnapshot,
    TauResumeSnapshotUploadRequest,
)
from ms_tau_sdk.errors import BackendConflictError
from ms_tau_sdk.sessions.storage import SESSION_ENTRY_ADAPTER, serialize_entry

SNAPSHOT_SCHEMA_VERSION = 2
TAU_RUNTIME_VERSION = version("tau-ai")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class RestoredSnapshot:
    entries: list[SessionEntry]
    next_sequence: int


def restore_snapshot(
    *,
    snapshot: TauResumeSnapshot,
    history_delta: SessionEntryList,
    runtime_config_sha256: str,
) -> RestoredSnapshot:
    if snapshot.snapshot_schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise BackendConflictError("Tau resume snapshot schema is unsupported")
    if snapshot.tau_runtime_version != TAU_RUNTIME_VERSION:
        raise BackendConflictError("Tau resume snapshot runtime version is incompatible")
    if snapshot.runtime_config_sha256 != runtime_config_sha256:
        raise BackendConflictError("Tau resume snapshot runtime configuration changed")
    if sha256_json(snapshot.snapshot) != snapshot.payload_sha256:
        raise BackendConflictError("Tau resume snapshot payload hash is invalid")
    if snapshot.snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise BackendConflictError("Tau resume snapshot payload schema is invalid")
    raw_entries = snapshot.snapshot.get("entries")
    if not isinstance(raw_entries, list):
        raise BackendConflictError("Tau resume snapshot entries are invalid")
    if len(raw_entries) != snapshot.base_sequence:
        raise BackendConflictError("Tau resume snapshot sequence boundary is invalid")
    entries = [SESSION_ENTRY_ADAPTER.validate_python(entry) for entry in raw_entries]
    expected_sequence = snapshot.base_sequence
    for record in history_delta.entries:
        if record.sequence != expected_sequence:
            raise BackendConflictError("Tau resume snapshot delta is not contiguous")
        entries.append(SESSION_ENTRY_ADAPTER.validate_python(record.entry_json))
        expected_sequence += 1
    if expected_sequence != history_delta.next_sequence:
        raise BackendConflictError("Tau resume snapshot delta boundary is inconsistent")
    return RestoredSnapshot(entries=entries, next_sequence=expected_sequence)


def build_snapshot_upload(
    *,
    holder_id: str,
    lease_token: str,
    entries: list[SessionEntry],
    base_sequence: int,
    last_committed_turn_uid: str,
    runtime_config_sha256: str,
) -> TauResumeSnapshotUploadRequest:
    if len(entries) != base_sequence:
        raise BackendConflictError("Local Tau entries do not match the durable snapshot boundary")
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "entries": [serialize_entry(entry) for entry in entries],
    }
    return TauResumeSnapshotUploadRequest(
        holder_id=holder_id,
        lease_token=lease_token,
        base_sequence=base_sequence,
        last_committed_turn_uid=last_committed_turn_uid,
        snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
        tau_runtime_version=TAU_RUNTIME_VERSION,
        runtime_config_sha256=runtime_config_sha256,
        payload_sha256=sha256_json(payload),
        snapshot=payload,
    )
