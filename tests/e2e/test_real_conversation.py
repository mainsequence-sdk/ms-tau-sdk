"""Opt-in real Tau conversation and UX verification through Docker Compose."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest

SESSION_UID = os.getenv("ASTRO_REAL_CONVERSATION_SESSION_UID", "").strip()
SECOND_SESSION_UID = os.getenv("ASTRO_REAL_CONVERSATION_SECOND_SESSION_UID", "").strip()
BASE_URL = os.getenv("ASTRO_REAL_CONVERSATION_URL", "http://127.0.0.1:8787").rstrip("/")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = Path(
    os.getenv(
        "ASTRO_REAL_CONVERSATION_REPORT_PATH",
        str(REPOSITORY_ROOT / ".astro/test-results/real-conversation-timing.json"),
    )
)

MAX_RESPONSE_HEADERS_SECONDS = float(
    os.getenv("ASTRO_REAL_CONVERSATION_MAX_RESPONSE_HEADERS_SECONDS", "0.25")
)
MAX_FIRST_EVENT_SECONDS = float(os.getenv("ASTRO_REAL_CONVERSATION_MAX_FIRST_EVENT_SECONDS", "0.5"))
MAX_TTFT_SECONDS = float(os.getenv("ASTRO_REAL_CONVERSATION_MAX_TTFT_SECONDS", "15"))
MAX_SSE_GAP_SECONDS = float(os.getenv("ASTRO_REAL_CONVERSATION_MAX_SSE_GAP_SECONDS", "10"))
MAX_TURN_SECONDS = float(os.getenv("ASTRO_REAL_CONVERSATION_MAX_TURN_SECONDS", "45"))
MAX_DURABILITY_SECONDS = float(os.getenv("ASTRO_REAL_CONVERSATION_MAX_DURABILITY_SECONDS", "2"))
MAX_RESTART_READY_SECONDS = float(
    os.getenv("ASTRO_REAL_CONVERSATION_MAX_RESTART_READY_SECONDS", "10")
)
MAX_SNAPSHOT_PUBLICATION_SECONDS = float(
    os.getenv("ASTRO_REAL_CONVERSATION_MAX_SNAPSHOT_PUBLICATION_SECONDS", "2")
)

UX_BUDGETS_SECONDS = {
    "response_headers": MAX_RESPONSE_HEADERS_SECONDS,
    "first_event": MAX_FIRST_EVENT_SECONDS,
    "time_to_first_text": MAX_TTFT_SECONDS,
    "maximum_sse_gap": MAX_SSE_GAP_SECONDS,
    "turn_total": MAX_TURN_SECONDS,
    "durability": MAX_DURABILITY_SECONDS,
    "restart_to_ready": MAX_RESTART_READY_SECONDS,
}

pytestmark = [
    pytest.mark.timeout(360),
    pytest.mark.skipif(
        not SESSION_UID,
        reason="ASTRO_REAL_CONVERSATION_SESSION_UID is required",
    ),
]


@dataclass(frozen=True)
class TurnResult:
    text: str
    response_headers_seconds: float
    first_event_seconds: float
    time_to_first_text_seconds: float
    last_text_seconds: float
    finish_seconds: float
    done_seconds: float
    maximum_sse_gap_seconds: float
    sse_event_count: int
    text_delta_count: int
    event_type_counts: dict[str, int]

    @property
    def output_stream_seconds(self) -> float:
        return self.last_text_seconds - self.time_to_first_text_seconds

    @property
    def durability_seconds(self) -> float:
        return self.done_seconds - self.finish_seconds

    def timing_report(self) -> dict[str, object]:
        return {
            "response_headers_seconds": _rounded(self.response_headers_seconds),
            "first_event_seconds": _rounded(self.first_event_seconds),
            "time_to_first_text_seconds": _rounded(self.time_to_first_text_seconds),
            "output_stream_seconds": _rounded(self.output_stream_seconds),
            "finish_seconds": _rounded(self.finish_seconds),
            "durability_seconds": _rounded(self.durability_seconds),
            "total_seconds": _rounded(self.done_seconds),
            "maximum_sse_gap_seconds": _rounded(self.maximum_sse_gap_seconds),
            "assistant_text_characters": len(self.text),
            "sse_event_count": self.sse_event_count,
            "text_delta_count": self.text_delta_count,
            "event_type_counts": dict(sorted(self.event_type_counts.items())),
        }


def _rounded(value: float) -> float:
    return round(value, 3)


def _run(*command: str, timeout_seconds: float = 180) -> str:
    result = subprocess.run(
        list(command),
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        raise AssertionError(
            f"Command {' '.join(command)} failed with exit code {result.returncode}:\n{output}"
        )
    return result.stdout.strip()


def _inspect_astro_container() -> dict[str, object]:
    container_ids = [
        value for value in _run("docker", "compose", "ps", "--quiet", "astro").splitlines() if value
    ]
    assert len(container_ids) == 1, "Compose must have exactly one Astro container"
    container_id = container_ids[0]
    inspection = json.loads(_run("docker", "inspect", container_id))
    assert isinstance(inspection, list) and len(inspection) == 1
    container = inspection[0]
    assert container["State"]["Running"] is True, "Astro Compose container is not running"
    assert container["Config"]["Labels"]["com.docker.compose.service"] == "astro"
    return {
        "container_id": container_id[:12],
        "image": container["Config"]["Image"],
        "started_at": container["State"]["StartedAt"],
    }


def _start_compose() -> tuple[dict[str, object], float]:
    started_at = time.monotonic()
    _run(
        "docker",
        "compose",
        "up",
        "--build",
        "--force-recreate",
        "--detach",
        "astro",
        timeout_seconds=240,
    )
    return _inspect_astro_container(), time.monotonic() - started_at


def _wait_until_ready(
    client: httpx.Client,
    *,
    timeout_seconds: float = 120,
) -> tuple[dict[str, Any], float]:
    started_at = time.monotonic()
    deadline = started_at + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = client.get("/ready")
            if response.status_code == 200:
                payload = response.json()
                assert payload["runtime"] == "tau"
                return payload, time.monotonic() - started_at
        except (httpx.HTTPError, ValueError, KeyError) as error:
            last_error = error
        time.sleep(0.5)
    raise AssertionError(f"Astro did not become ready: {last_error}")


def _assert_response_came_from_container(
    ready_payload: dict[str, Any],
    container: dict[str, object],
) -> None:
    holder_hostname = str(ready_payload.get("holder_id") or "").split(":", 1)[0]
    container_id = str(container["container_id"])
    assert holder_hostname == container_id, (
        "The local HTTP response did not come from the Compose Astro container"
    )


def _stream_turn(
    client: httpx.Client,
    prompt: str,
    *,
    session_uid: str = SESSION_UID,
) -> TurnResult:
    started_at = time.monotonic()
    response_headers_at: float | None = None
    first_event_at: float | None = None
    first_text_at: float | None = None
    last_text_at: float | None = None
    finish_at: float | None = None
    done_at: float | None = None
    previous_event_at: float | None = None
    maximum_sse_gap_seconds = 0.0
    text_parts: list[str] = []
    finish_count = 0
    sse_event_count = 0
    text_delta_count = 0
    event_types: Counter[str] = Counter()

    with client.stream(
        "POST",
        "/api/chat",
        json={"sessionUid": session_uid, "message": prompt},
    ) as response:
        response_headers_at = time.monotonic()
        response.raise_for_status()
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"

        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line.removeprefix("data: ")
            observed_at = time.monotonic()
            if first_event_at is None:
                first_event_at = observed_at
            if previous_event_at is not None:
                maximum_sse_gap_seconds = max(
                    maximum_sse_gap_seconds,
                    observed_at - previous_event_at,
                )
            previous_event_at = observed_at

            if payload == "[DONE]":
                done_at = observed_at
                break

            event = json.loads(payload)
            event_type = str(event.get("type") or "unknown")
            event_types[event_type] += 1
            sse_event_count += 1
            if event_type == "error":
                pytest.fail(f"Real conversation returned an error event: {event}")
            if event_type == "text-delta":
                delta = str(event.get("textDelta") or "")
                if delta:
                    text_delta_count += 1
                    if first_text_at is None:
                        first_text_at = observed_at
                    last_text_at = observed_at
                    text_parts.append(delta)
            if event_type == "finish":
                finish_count += 1
                finish_at = observed_at

    assert response_headers_at is not None
    assert first_event_at is not None, "Conversation returned no SSE events"
    assert first_text_at is not None, "Conversation returned no assistant text"
    assert last_text_at is not None
    assert finish_at is not None, "Conversation returned no finish event"
    assert done_at is not None, "Conversation did not terminate with [DONE]"
    assert finish_count == 1, "Conversation must emit exactly one finish event"
    assert response_headers_at <= first_event_at <= first_text_at <= finish_at <= done_at

    return TurnResult(
        text="".join(text_parts).strip(),
        response_headers_seconds=response_headers_at - started_at,
        first_event_seconds=first_event_at - started_at,
        time_to_first_text_seconds=first_text_at - started_at,
        last_text_seconds=last_text_at - started_at,
        finish_seconds=finish_at - started_at,
        done_seconds=done_at - started_at,
        maximum_sse_gap_seconds=maximum_sse_gap_seconds,
        sse_event_count=sse_event_count,
        text_delta_count=text_delta_count,
        event_type_counts=dict(event_types),
    )


def _turn_budget_failures(label: str, result: TurnResult) -> list[str]:
    measurements = {
        "response_headers": result.response_headers_seconds,
        "first_event": result.first_event_seconds,
        "time_to_first_text": result.time_to_first_text_seconds,
        "maximum_sse_gap": result.maximum_sse_gap_seconds,
        "turn_total": result.done_seconds,
        "durability": result.durability_seconds,
    }
    return [
        (f"{label}.{metric}={_rounded(observed)}s exceeded {_rounded(UX_BUDGETS_SECONDS[metric])}s")
        for metric, observed in measurements.items()
        if observed > UX_BUDGETS_SECONDS[metric]
    ]


def _wait_for_snapshot_upload(
    client: httpx.Client,
    *,
    after_count: int,
) -> tuple[dict[str, Any], float]:
    started_at = time.monotonic()
    deadline = started_at + MAX_SNAPSHOT_PUBLICATION_SECONDS
    while time.monotonic() < deadline:
        response = client.get("/ready")
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("snapshot_upload_count") or 0) > after_count:
            return payload, time.monotonic() - started_at
        time.sleep(0.05)
    raise AssertionError("Tau resume snapshot was not published after durable [DONE]")


def _write_report(report: dict[str, object]) -> None:
    path = REPORT_PATH if REPORT_PATH.is_absolute() else REPOSITORY_ROOT / REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_real_conversation_in_container_meets_ux_budgets() -> None:
    parsed_url = urlparse(BASE_URL)
    assert parsed_url.hostname in {"127.0.0.1", "localhost", "::1"}, (
        "The real conversation test must target the local Compose container"
    )

    report: dict[str, object] = {
        "started_at": datetime.now(UTC).isoformat(),
        "session_uid": SESSION_UID,
        "base_url": BASE_URL,
        "ux_budgets_seconds": UX_BUDGETS_SECONDS,
        "completed": False,
    }
    token = f"tau-{uuid.uuid4().hex[:12]}"
    timeout = httpx.Timeout(
        connect=10,
        read=max(180, MAX_TURN_SECONDS + MAX_DURABILITY_SECONDS + 30),
        write=30,
        pool=10,
    )

    try:
        container, compose_up_seconds = _start_compose()
        report["container"] = container
        report["compose_up_seconds"] = _rounded(compose_up_seconds)

        with httpx.Client(base_url=BASE_URL, timeout=timeout) as client:
            ready, initial_ready_seconds = _wait_until_ready(client)
            _assert_response_came_from_container(ready, container)
            report["initial_ready_seconds"] = _rounded(initial_ready_seconds)
            report["initial_holder_id"] = ready["holder_id"]
            snapshot_upload_count = int(ready.get("snapshot_upload_count") or 0)

            first = _stream_turn(
                client,
                (
                    "Remember this exact verification token for this conversation: "
                    f"{token}. Reply with only the token."
                ),
            )
            assert token in first.text, "The first turn did not return the verification token"
            report["first_turn"] = first.timing_report()

            _snapshot_ready, first_snapshot_seconds = _wait_for_snapshot_upload(
                client,
                after_count=snapshot_upload_count,
            )
            report["first_snapshot_publication_seconds"] = _rounded(first_snapshot_seconds)
            snapshot_upload_count += 1

            warm = _stream_turn(
                client,
                "Reply with only the exact verification token from the prior turn.",
            )
            assert token in warm.text, "The warm turn lost loaded-session context"
            report["warm_turn"] = warm.timing_report()
            _snapshot_ready, warm_snapshot_seconds = _wait_for_snapshot_upload(
                client,
                after_count=snapshot_upload_count,
            )
            report["warm_snapshot_publication_seconds"] = _rounded(warm_snapshot_seconds)

            if SECOND_SESSION_UID:
                second_session = _stream_turn(
                    client,
                    "Reply with only: second-session-ok",
                    session_uid=SECOND_SESSION_UID,
                )
                assert "second-session-ok" in second_session.text.lower()
                report["second_session_turn"] = second_session.timing_report()

        restart_started_at = time.monotonic()
        _run("docker", "compose", "restart", "astro", timeout_seconds=150)
        restart_command_seconds = time.monotonic() - restart_started_at
        restarted_container = _inspect_astro_container()
        assert restarted_container["container_id"] == container["container_id"]

        with httpx.Client(base_url=BASE_URL, timeout=timeout) as client:
            ready, restart_ready_seconds = _wait_until_ready(client)
            _assert_response_came_from_container(ready, restarted_container)
            assert ready["holder_id"] != report["initial_holder_id"], (
                "Astro runtime holder did not change after the container restart"
            )
            restart_to_ready_seconds = restart_command_seconds + restart_ready_seconds
            report["restart"] = {
                "command_seconds": _rounded(restart_command_seconds),
                "ready_seconds": _rounded(restart_ready_seconds),
                "total_seconds": _rounded(restart_to_ready_seconds),
                "holder_id": ready["holder_id"],
            }

            resumed = _stream_turn(
                client,
                "Reply with only the exact verification token I asked you to remember.",
            )
            assert token in resumed.text, (
                "The resumed turn did not recover the Django-persisted conversation"
            )
            report["resumed_turn"] = resumed.timing_report()
            assert int(ready.get("mcp_tool_count") or 0) > 0

            post_resume_ready = client.get("/ready").json()
            assert int(post_resume_ready.get("snapshot_restore_count") or 0) >= 1, (
                "Restarted Astro did not restore the compatible Tau snapshot"
            )
            report["restart"]["snapshot_restore_count"] = int(
                post_resume_ready["snapshot_restore_count"]
            )

        failures = [
            *_turn_budget_failures("first_turn", first),
            *_turn_budget_failures("warm_turn", warm),
            *_turn_budget_failures("resumed_turn", resumed),
        ]
        if SECOND_SESSION_UID:
            failures.extend(_turn_budget_failures("second_session_turn", second_session))
        if restart_to_ready_seconds > MAX_RESTART_READY_SECONDS:
            failures.append(
                "restart.total="
                f"{_rounded(restart_to_ready_seconds)}s exceeded "
                f"{_rounded(MAX_RESTART_READY_SECONDS)}s"
            )
        report["ux_budget_failures"] = failures
        report["ux_passed"] = not failures
        report["completed"] = True
        report["finished_at"] = datetime.now(UTC).isoformat()
        _write_report(report)
        print(json.dumps(report, indent=2, sort_keys=True))

        assert not failures, "UX timing budgets failed:\n" + "\n".join(failures)
    except BaseException as error:
        report["failure"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        report["finished_at"] = datetime.now(UTC).isoformat()
        _write_report(report)
        raise
