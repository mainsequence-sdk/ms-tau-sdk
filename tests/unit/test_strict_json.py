import pytest

from ms_tau_sdk.protocols.strict_json import (
    StrictJsonError,
    build_repair_prompt,
    build_strict_json_contract,
    validate_strict_json,
)


def test_json_schema_contract_validates_and_canonicalizes():
    contract = build_strict_json_contract(
        {
            "type": "json_schema",
            "strict": True,
            "schema": {
                "type": "object",
                "required": ["count"],
                "properties": {"count": {"type": "integer", "minimum": 1}},
                "additionalProperties": False,
            },
        }
    )

    canonical, value = validate_strict_json(' { "count": 2 } ', contract)

    assert canonical == '{"count":2}'
    assert value == {"count": 2}


def test_json_schema_contract_rejects_invalid_value_and_remote_ref():
    contract = build_strict_json_contract(
        {
            "type": "json_schema",
            "strict": True,
            "schema": {
                "type": "object",
                "required": ["count"],
                "properties": {"count": {"type": "integer"}},
            },
        }
    )
    with pytest.raises(StrictJsonError, match="'count' is a required property"):
        validate_strict_json("{}", contract)

    remote_ref = build_strict_json_contract(
        {
            "type": "json_schema",
            "strict": True,
            "schema": {"$ref": "https://example.test/schema.json"},
        }
    )
    with pytest.raises(StrictJsonError, match="non-local"):
        validate_strict_json("{}", remote_ref)


def test_repair_prompt_is_bounded_by_contract():
    contract = build_strict_json_contract(
        "json_object",
        force_strict=True,
        repair_attempts=99,
    )

    prompt = build_repair_prompt(
        invalid_text="not-json",
        validation_error="invalid",
        contract=contract,
        attempt=1,
    )

    assert contract.repair_attempts == 10
    assert "1 of 10" in prompt
    assert "not-json" in prompt
