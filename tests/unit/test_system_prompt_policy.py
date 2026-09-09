from astro.resources.loader import append_system_prompt


def test_system_prompt_requires_fresh_current_turn_action_attempts() -> None:
    prompt = append_system_prompt()
    action_rule = (
        "When the user explicitly requests a supported action, attempt the relevant tool "
        "in the current\n  turn."
    )

    assert "## Current-turn execution discipline" in prompt
    assert action_rule in prompt
    assert (
        "Never reuse a previous tool failure, stale lookup, or earlier runtime state as evidence"
        in prompt
    )
    assert "Report a blocker only after a current-turn tool call fails" in prompt
    assert (
        "Do not infer missing user permissions or recommend a different execution context" in prompt
    )
