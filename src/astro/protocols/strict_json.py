"""Bounded strict-JSON validation and repair contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

JsonMode = Literal["none", "json", "json_object", "json_schema"]
MAX_SCHEMA_BYTES = 64 * 1024
MAX_REPAIR_ATTEMPTS = 10


class StrictJsonError(ValueError):
    """A model response did not satisfy its strict JSON contract."""


@dataclass(frozen=True, slots=True)
class StrictJsonContract:
    mode: JsonMode = "none"
    schema: dict[str, Any] | None = None
    repair_attempts: int = 3

    @property
    def enabled(self) -> bool:
        return self.mode != "none"


def _json_mode(value: object) -> JsonMode:
    if not isinstance(value, str):
        return "none"
    normalized = value.strip().lower()
    if normalized in {"json", "application/json"}:
        return "json"
    if normalized in {"json_object", "object", "dictionary", "dict"}:
        return "json_object"
    if normalized == "json_schema":
        return "json_schema"
    return "none"


def _schema_from_format(value: dict[str, Any]) -> dict[str, Any] | None:
    schema = value.get("schema")
    if isinstance(schema, dict):
        return schema
    wrapper = value.get("json_schema")
    if isinstance(wrapper, dict) and isinstance(wrapper.get("schema"), dict):
        return cast(dict[str, Any], wrapper["schema"])
    return None


def build_strict_json_contract(
    response_format: object,
    *,
    force_strict: bool = False,
    repair_attempts: int = 3,
) -> StrictJsonContract:
    mode = _json_mode(response_format)
    schema: dict[str, Any] | None = None
    if isinstance(response_format, dict):
        mode = _json_mode(response_format.get("type"))
        if mode == "none":
            mode = _json_mode(response_format.get("format"))
        declared_strict = response_format.get("strict") is True
        force_strict = force_strict or declared_strict
        if mode == "json_schema":
            schema = _schema_from_format(response_format)
    if force_strict and mode == "none":
        mode = "json"
    attempts = max(0, min(int(repair_attempts), MAX_REPAIR_ATTEMPTS))
    return StrictJsonContract(
        mode=mode,
        schema=schema,
        repair_attempts=attempts,
    )


def _assert_local_refs(value: object) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and not reference.startswith("#"):
            raise StrictJsonError("JSON schema contains a non-local $ref")
        for child in value.values():
            _assert_local_refs(child)
    elif isinstance(value, list):
        for child in value:
            _assert_local_refs(child)


def validate_strict_json(
    text: str,
    contract: StrictJsonContract,
) -> tuple[str, Any]:
    if not text.strip():
        raise StrictJsonError("Response text is empty")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise StrictJsonError(str(error)) from error
    if contract.mode in {"json_object", "json_schema"} and not isinstance(value, dict):
        raise StrictJsonError("Response must be a JSON object")
    if contract.mode == "json_schema":
        if contract.schema is None:
            raise StrictJsonError("A strict json_schema response requires a schema")
        encoded_schema = json.dumps(contract.schema, separators=(",", ":"))
        if len(encoded_schema.encode()) > MAX_SCHEMA_BYTES:
            raise StrictJsonError("JSON schema exceeds the configured size limit")
        _assert_local_refs(contract.schema)
        try:
            Draft202012Validator.check_schema(contract.schema)
            Draft202012Validator(contract.schema).validate(value)
        except SchemaError as error:
            raise StrictJsonError(f"Invalid JSON schema: {error.message}") from error
        except ValidationError as error:
            location = ".".join(str(part) for part in error.absolute_path)
            prefix = f"{location}: " if location else ""
            raise StrictJsonError(f"{prefix}{error.message}") from error
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False), value


def build_repair_prompt(
    *,
    invalid_text: str,
    validation_error: str,
    contract: StrictJsonContract,
    attempt: int,
) -> str:
    schema = (
        f"\nJSON schema: {json.dumps(contract.schema, separators=(',', ':'))}"
        if contract.schema
        else ""
    )
    return (
        "Repair this assistant response into valid JSON.\n"
        f"Repair attempt: {attempt} of {contract.repair_attempts}.\n"
        f"Required response format: {contract.mode}.\n"
        f"Validation error: {validation_error}.{schema}\n"
        "Return only corrected JSON. Do not include prose, Markdown fences, "
        "explanations, or comments.\n"
        f"Original invalid response:\n{invalid_text}"
    )
