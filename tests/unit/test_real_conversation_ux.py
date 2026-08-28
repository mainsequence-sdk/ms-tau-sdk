from __future__ import annotations

from tests.e2e import test_real_conversation as conversation


def _turn(**overrides: object) -> conversation.TurnResult:
    values: dict[str, object] = {
        "text": "ok",
        "response_headers_seconds": 0.2,
        "first_event_seconds": 0.4,
        "time_to_first_text_seconds": 0.8,
        "last_text_seconds": 1.1,
        "finish_seconds": 1.2,
        "done_seconds": 1.4,
        "maximum_sse_gap_seconds": 0.3,
        "sse_event_count": 5,
        "text_delta_count": 2,
        "event_type_counts": {"text-delta": 2, "finish": 1},
    }
    values.update(overrides)
    return conversation.TurnResult(**values)  # type: ignore[arg-type]


def test_turn_timing_report_exposes_user_visible_and_durability_milestones():
    report = _turn().timing_report()

    assert report == {
        "response_headers_seconds": 0.2,
        "first_event_seconds": 0.4,
        "time_to_first_text_seconds": 0.8,
        "output_stream_seconds": 0.3,
        "finish_seconds": 1.2,
        "durability_seconds": 0.2,
        "total_seconds": 1.4,
        "maximum_sse_gap_seconds": 0.3,
        "assistant_text_characters": 2,
        "sse_event_count": 5,
        "text_delta_count": 2,
        "event_type_counts": {"finish": 1, "text-delta": 2},
    }


def test_turn_budget_failures_name_every_exceeded_ux_measurement(monkeypatch):
    for metric in (
        "response_headers",
        "first_event",
        "time_to_first_text",
        "maximum_sse_gap",
        "turn_total",
        "durability",
    ):
        monkeypatch.setitem(conversation.UX_BUDGETS_SECONDS, metric, 0.1)

    failures = conversation._turn_budget_failures("turn", _turn())

    assert [failure.split("=", 1)[0] for failure in failures] == [
        "turn.response_headers",
        "turn.first_event",
        "turn.time_to_first_text",
        "turn.maximum_sse_gap",
        "turn.turn_total",
        "turn.durability",
    ]


def test_container_identity_must_match_ready_holder():
    conversation._assert_response_came_from_container(
        {"holder_id": "abc123def456:runtime"},
        {"container_id": "abc123def456"},
    )
