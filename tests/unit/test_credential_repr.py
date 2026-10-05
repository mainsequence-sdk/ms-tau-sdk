"""Credential values never appear in repr(), str(), a settings error, or a log rendering.

Each object here holds a credential that the SDK reads or sends: the runtime credential secret,
a Main Sequence access token, or a model provider key. The code that uses a value still reads it
unchanged, but printing the object, logging it, or a settings validation error never shows it
(#57). Every exchange is answered by a stand-in transport, and the values are stand-ins.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import structlog
from pydantic import ValidationError

from ms_tau_sdk.backend import auth as auth_module
from ms_tau_sdk.backend.auth import AccessToken, RuntimeCredentialAuth
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.models import (
    ProviderControl,
    ProviderCredential,
    ProviderExecutionEvidence,
    TauRuntimeBootstrap,
)
from ms_tau_sdk.logging import configure_logging
from ms_tau_sdk.providers.factory import ProviderRuntime
from ms_tau_sdk.settings import TauSDKSettings

# Random bodies, so that any run of eight characters identifies the value it came from.
SECRET = "rcs-q7Lm2Xc9RfB4nZ8kW1pD"
ACCESS_TOKEN = "at-Vb4Nz8Kw1PhD6tY3jS5gQ"
REFRESH_TOKEN = "rt-Hd6Ty3Js5GmW2pE7uA4xC"
PROVIDER_KEY = "pk-Wp2Qe7Ua4MkC8fR1yO6tZ"
HEADER_KEY = "hk-Zc8Fr1Yo6TnB3vL9sX5qJ"
CREDENTIALS = (SECRET, ACCESS_TOKEN, REFRESH_TOKEN, PROVIDER_KEY, HEADER_KEY)


def shows(text: str, value: str) -> bool:
    """Whether ``text`` shows ``value`` or any run of eight of its characters."""
    return any(value[start : start + 8] in text for start in range(len(value) - 7))


def renderings(value: object) -> list[str]:
    return [repr(value), str(value)]


def assert_shows_no_credential(text: str) -> None:
    shown = [value for value in CREDENTIALS if shows(text, value)]
    assert shown == [], f"credential values shown in: {text}"


def managed_settings(tmp_path: Any) -> TauSDKSettings:
    return TauSDKSettings(
        _env_file=None,
        backend_url="https://backend.test",
        runtime_credential_id="credential-id",
        runtime_credential_secret=SECRET,
        workspace=tmp_path,
    )


def custom_provider_credential() -> ProviderCredential:
    """An organization_custom credential: its key travels in a header, not in api_key."""
    return MainSequenceClient.provider_credential_from_hydration(
        "acme-gateway",
        {
            "credentials": {
                "acme-gateway": {
                    "credential_kind": "organization_custom",
                    "credential": {
                        "type": "organization_custom",
                        "base_url": "https://models.example.test/v1",
                        "api": "openai-completions",
                        "headers": {"x-api-key": HEADER_KEY},
                    },
                }
            }
        },
    )


def provider_control(provider: str) -> ProviderControl:
    return ProviderControl(
        schema_version=1,
        catalog_digest=f"sha256:{'0' * 64}",
        provider=provider,
        model={
            "model": "test-model",
            "api": "openai-completions",
            "input": ["text"],
            "reasoning": False,
            "thinking_levels": [],
        },
    )


def runtime_bootstrap() -> TauRuntimeBootstrap:
    """A bootstrap answer as the backend sends it, with the hydrated provider credential."""
    return TauRuntimeBootstrap.model_validate(
        {
            "session": {
                "uid": "session-1",
                "harness": "tau",
                "harness_protocol": "tau-session-v1",
                "harness_version": "1",
                "active_provider": "anthropic",
                "active_model": "test-model",
            },
            "lease": {
                "lease_token": "lease-1",
                "holder_id": "holder-1",
                "lease_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            },
            "runtime_state": {
                "harness": "tau",
                "harness_protocol": "tau-session-v1",
                "harness_version": "1",
            },
            "history": {"entries": [], "next_sequence": 0},
            "provider_credentials": {
                "credentials": {
                    "anthropic": {
                        "credential_kind": "api_key",
                        "credential": {"api_key": PROVIDER_KEY},
                        "version": 1,
                        "credential_hash": "sha256:stand-in",
                    }
                }
            },
            "provider_control": provider_control("anthropic").model_dump(),
            "runtime_capabilities": {},
        }
    )


def test_settings_read_the_runtime_credential_secret_but_never_show_it(monkeypatch, tmp_path):
    monkeypatch.setenv("MAINSEQUENCE_RUNTIME_CREDENTIAL_ID", "credential-id")
    monkeypatch.setenv("MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET", SECRET)

    settings = TauSDKSettings(_env_file=None, workspace=tmp_path)

    # The value is still a plain string for the code that reads it.
    assert settings.runtime_credential_secret == SECRET
    for shown in renderings(settings):
        assert_shows_no_credential(shown)
        assert "runtime_credential_id='credential-id'" in shown


@pytest.mark.parametrize(
    ("environment", "problem"),
    [
        pytest.param(
            {
                "MAINSEQUENCE_AUTH_MODE": "jwt",
                "MAINSEQUENCE_RUNTIME_CREDENTIAL_ID": "credential-id",
                "MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET": SECRET,
            },
            "MAINSEQUENCE_AUTH_MODE=jwt is supported only when TAU_LOCAL_MODE=true",
            id="runtime-credential-secret",
        ),
        pytest.param(
            {
                "MAINSEQUENCE_AUTH_MODE": "jwt",
                "MAINSEQUENCE_ACCESS_TOKEN": ACCESS_TOKEN,
                "MAINSEQUENCE_REFRESH_TOKEN": REFRESH_TOKEN,
            },
            "MAINSEQUENCE_AUTH_MODE=jwt is supported only when TAU_LOCAL_MODE=true",
            id="local-token-pair",
        ),
    ],
)
def test_a_settings_error_names_the_problem_without_echoing_configured_values(
    monkeypatch, environment, problem
):
    # Only the credential settings are configured, as in a deployment, so the credentials are the
    # last values of the input that an error could echo.
    monkeypatch.delenv("MAINSEQUENCE_TAU_STATE_ROOT", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError) as rejected:
        TauSDKSettings(_env_file=None)

    for shown in renderings(rejected.value):
        assert problem in shown
        assert "input_value" not in shown
        assert_shows_no_credential(shown)


async def test_the_exchange_sends_the_secret_and_the_access_token_is_never_shown(tmp_path):
    settings = managed_settings(tmp_path)
    sent: list[dict[str, Any]] = []

    def exchange(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200, json={"access": ACCESS_TOKEN, "token_type": "Bearer", "expires_in": 900}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(exchange)) as client:
        auth = RuntimeCredentialAuth(settings, exchange_client=client)
        headers = await auth.headers()

    # The secret is read and sent, and the access token is used, exactly as before.
    assert sent == [{"credential_id": "credential-id", "credential_secret": SECRET}]
    assert headers == {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    token = AccessToken(value=ACCESS_TOKEN, token_type="Bearer", expires_at=None)
    assert token.value == ACCESS_TOKEN
    # The CLI's answer is reduced to the token before anything else sees it.
    answer = auth_module._read_cli_token(
        json.dumps(
            {
                "endpoint": "https://backend.test",
                "access_token": ACCESS_TOKEN,
                "token_type": "Bearer",
                "expires_at": None,
            }
        ),
        "",
    )
    assert answer.token is not None and answer.token.value == ACCESS_TOKEN
    for holder in (token, answer, vars(auth)):
        for shown in renderings(holder):
            assert_shows_no_credential(shown)


def test_provider_credentials_are_used_but_never_shown():
    custom = custom_provider_credential()
    keyed = MainSequenceClient.provider_credential_from_hydration(
        "anthropic",
        {"credentials": {"anthropic": {"credential": {"api_key": PROVIDER_KEY}}}},
    )
    evidence = ProviderExecutionEvidence(
        credential=custom, provider_control=provider_control("acme-gateway")
    )
    runtime = ProviderRuntime(
        name="acme-gateway",
        model="test-model",
        thinking_level="off",
        provider=object(),
        credential=custom,
        provider_control=evidence.provider_control,
    )

    assert custom.headers == {"x-api-key": HEADER_KEY}
    assert keyed.secret() == PROVIDER_KEY
    for holder in (custom, keyed, evidence, runtime):
        for shown in renderings(holder):
            assert_shows_no_credential(shown)
            assert "acme-gateway" in shown or "anthropic" in shown


def test_a_runtime_bootstrap_hands_over_provider_credentials_but_never_shows_them():
    bootstrap = runtime_bootstrap()

    credential = MainSequenceClient.provider_credential_from_hydration(
        "anthropic", bootstrap.provider_credentials
    )
    assert credential.secret() == PROVIDER_KEY
    for shown in renderings(bootstrap):
        assert_shows_no_credential(shown)
        assert "session-1" in shown


@pytest.mark.parametrize("sink", ["machine", "human"])
def test_a_log_event_that_carries_credential_objects_never_shows_their_values(
    sink, capsys, tmp_path
):
    configure_logging("DEBUG", machine_sink=sink == "machine", human_sink=sink == "human")

    # None of these field names is one the log redaction recognizes, so only the objects' own
    # renderings keep the values out.
    structlog.get_logger("ms_tau_sdk.test").info(
        "test.credential_objects",
        settings=managed_settings(tmp_path),
        access=AccessToken(value=ACCESS_TOKEN, token_type="Bearer", expires_at=None),
        provider=custom_provider_credential(),
        bootstrap=runtime_bootstrap(),
    )

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "test.credential_objects" in output
    assert "credential-id" in output
    assert_shows_no_credential(output)
