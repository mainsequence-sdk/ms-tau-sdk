import pytest
from fastapi import HTTPException

from astro.api.llm import (
    LlmMessage,
    ResponseFormat,
    _history_and_prompt,
    _strict_json_mode,
)


def test_stateless_history_requires_final_user_message():
    system, history, prompt = _history_and_prompt(
        [
            LlmMessage(role="system", content="Be precise."),
            LlmMessage(role="user", content="first"),
            LlmMessage(role="assistant", content="answer"),
            LlmMessage(role="user", content="second"),
        ]
    )

    assert system == "Be precise."
    assert len(history) == 2
    assert prompt == "second"


def test_stateless_history_rejects_assistant_after_final_user():
    with pytest.raises(HTTPException, match="final conversational message"):
        _history_and_prompt(
            [
                LlmMessage(role="user", content="first"),
                LlmMessage(role="assistant", content="answer"),
            ]
        )


def test_strict_json_mode_normalizes_aliases():
    assert _strict_json_mode("dictionary") == "json_object"
    assert (
        _strict_json_mode(ResponseFormat(type="json_object", strict=True))
        == "json_object"
    )
    assert _strict_json_mode(ResponseFormat(type="text")) is None
