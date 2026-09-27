import json
from importlib.metadata import version

import httpx
import pytest
from tau_agent import UserMessage
from tau_ai import OpenAICompatibleProvider
from tau_ai.env import OpenAICompatibleConfig

from ms_tau_sdk.logging import configure_logging
from ms_tau_sdk.providers.tau_compat import (
    AFFECTED_TAU_AI_VERSION,
    install_openai_compatible_provider_error_patch,
)
from ms_tau_sdk.runtime.events import TauRuntimeEvent
from ms_tau_sdk.runtime.failures import terminal_assistant_failure
from ms_tau_sdk.runtime.observability import TauTurnObserver


def _json_events(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines() if line.strip().startswith("{")]


def test_compatibility_patch_is_locked_to_the_exact_tau_dependency():
    assert version("tau-ai") == AFFECTED_TAU_AI_VERSION


@pytest.mark.asyncio
async def test_openai_compatible_patch_preserves_terminal_http_error_diagnostic(capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)
    install_openai_compatible_provider_error_patch()
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("", request=request)

    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            api_key="provider-secret",
            base_url="https://provider.example/v1",
            provider_name="provider-under-test",
            max_retries=2,
            max_retry_delay_seconds=0,
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider._client = client
        events = [
            event
            async for event in provider.stream_response(
                model="controlled-model",
                system="system",
                messages=[UserMessage(content="hello")],
                tools=[],
            )
        ]

    assert attempts == 3
    assert events[-1].type == "error"
    public_error = events[-1].model_dump(mode="json", by_alias=True)["error"]
    assert public_error["errorMessage"] == "ReadTimeout after 3 attempts"
    diagnostic = public_error["diagnostics"][0]["details"]
    assert diagnostic["error_type"] == "ReadTimeout"
    assert diagnostic["transport_phase"] == "read"
    assert diagnostic["attempts"] == 3
    assert diagnostic["retry_exhausted"] is True
    assert diagnostic["diagnostic_source"] == "ms-tau-sdk-tau-compat"
    assert diagnostic["failure_uid"]
    assert diagnostic["provider_duration_ms"] >= 0
    assert diagnostic["retry_history"] == [
        {
            "attempt": 2,
            "max_attempts": 3,
            "delay_seconds": 0.0,
            "error_type": "ReadTimeout",
        },
        {
            "attempt": 3,
            "max_attempts": 3,
            "delay_seconds": 0.0,
            "error_type": "ReadTimeout",
        },
    ]

    log_events = _json_events(capsys.readouterr().out)
    dependency_failure = next(
        event for event in log_events if event["event"] == "dependency.call.failed"
    )
    assert dependency_failure["error_type"] == "ReadTimeout"
    assert dependency_failure["provider_error_type"] == "ReadTimeout"
    assert dependency_failure["transport_phase"] == "read"
    assert dependency_failure["provider_attempts"] == 3
    assert dependency_failure["retry_exhausted"] is True
    assert dependency_failure["failure_uid"] == diagnostic["failure_uid"]
    assert dependency_failure["exception_frames"]
    serialized = json.dumps(dependency_failure)
    assert "provider-secret" not in serialized
    assert "authorization" not in serialized.lower()


def test_enriched_tau_diagnostic_survives_failure_extraction_and_observability(capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)
    message = {
        "role": "assistant",
        "stopReason": "error",
        "errorMessage": "ReadTimeout after 3 attempts",
        "diagnostics": [
            {
                "type": "provider_error",
                "details": {
                    "error_type": "ReadTimeout",
                    "transport_phase": "read",
                    "attempts": 3,
                    "retry_exhausted": True,
                    "provider_duration_ms": 180_828.798,
                    "diagnostic_source": "ms-tau-sdk-tau-compat",
                    "failure_uid": "provider-failure-1",
                },
            }
        ],
    }
    failure = terminal_assistant_failure({"message": message})

    assert failure is not None
    assert failure.error_type == "ProviderError"
    assert failure.provider_error_type == "ReadTimeout"
    assert failure.transport_phase == "read"
    assert failure.attempts == 3
    assert failure.retry_exhausted is True
    assert failure.provider_duration_ms == 180_828.798
    assert failure.failure_uid == "provider-failure-1"

    observer = TauTurnObserver(provider="provider-under-test", model="controlled-model")
    observer.observe(TauRuntimeEvent(type="message_start"))
    observer.observe(TauRuntimeEvent(type="message_end", data={"message": message}))

    events = _json_events(capsys.readouterr().out)
    model_failure = next(event for event in events if event["event"] == "agent.model.failed")
    assert model_failure["model_error_type"] == "ReadTimeout"
    assert model_failure["model_duration_ms"] == 180_828.798
    assert model_failure["provider_attempts"] == 3
    assert model_failure["transport_phase"] == "read"
    assert model_failure["retry_exhausted"] is True
    assert model_failure["failure_uid"] == "provider-failure-1"
    assert model_failure["provider_failure_summary"] == "ReadTimeout after 3 attempts"

    terminal_fields = observer.terminal_fields()
    assert terminal_fields["provider_error_type"] == "ReadTimeout"
    assert terminal_fields["provider_attempts"] == 3
    assert terminal_fields["provider_duration_ms"] == 180_828.798
    assert terminal_fields["failure_uid"] == "provider-failure-1"
