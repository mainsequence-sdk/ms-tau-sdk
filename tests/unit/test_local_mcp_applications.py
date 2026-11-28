"""Naming local applications in local mode (ADR 0032)."""

from __future__ import annotations

import asyncio
import socket
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from ms_tau_sdk.backend.models import MCPApplication
from ms_tau_sdk.errors import ConfigurationError
from ms_tau_sdk.settings import LocalMCPApplication, TauSDKSettings
from ms_tau_sdk.tools.mcp_applications import (
    application_connector,
    check_local_applications,
    local_application_connector,
)

LOCAL = {
    "auth_mode": "jwt",
    "local_mode": True,
    "access_token": "synthetic-access-token",
    "refresh_token": "synthetic-refresh-token",
}


def _settings(monkeypatch, raw: str | None = None, **values: Any) -> TauSDKSettings:
    if raw is None:
        monkeypatch.delenv("TAU_LOCAL_MCP_APPLICATIONS", raising=False)
    else:
        monkeypatch.setenv("TAU_LOCAL_MCP_APPLICATIONS", raw)
    return TauSDKSettings(_env_file=None, **(LOCAL | values))


def test_each_local_application_is_kept_by_name_and_origin_in_order(monkeypatch):
    settings = _settings(
        monkeypatch,
        '{"alpaca": "http://127.0.0.1:8000/", "orders": "https://localhost:9443", '
        '"ledger": "http://[::1]:7000"}',
    )

    assert settings.local_mcp_applications == (
        LocalMCPApplication(name="alpaca", origin="http://127.0.0.1:8000"),
        LocalMCPApplication(name="orders", origin="https://localhost:9443"),
        LocalMCPApplication(name="ledger", origin="http://[::1]:7000"),
    )


@pytest.mark.parametrize("raw", [None, "", "  ", "{}"])
def test_no_local_application_is_todays_behavior(monkeypatch, raw):
    assert _settings(monkeypatch, raw).local_mcp_applications == ()


def test_the_setting_given_in_code_is_checked_the_same_way(monkeypatch):
    monkeypatch.delenv("TAU_LOCAL_MCP_APPLICATIONS", raising=False)
    settings = TauSDKSettings(
        _env_file=None, **LOCAL, local_mcp_applications={"alpaca": "http://127.0.0.1:8000"}
    )

    assert settings.local_mcp_applications == (
        LocalMCPApplication(name="alpaca", origin="http://127.0.0.1:8000"),
    )
    with pytest.raises(ValidationError, match="must name a loopback host"):
        TauSDKSettings(
            _env_file=None, **LOCAL, local_mcp_applications={"alpaca": "http://10.0.0.5:8000"}
        )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ('{"Alpaca": "http://127.0.0.1:8000"}', "names an invalid application 'Alpaca'"),
        ('{"1alpaca": "http://127.0.0.1:8000"}', "names an invalid application '1alpaca'"),
        ('{"alpaca-api": "http://127.0.0.1:8000"}', "names an invalid application"),
        ('{"mainsequence": "http://127.0.0.1:8000"}', "names an invalid application"),
        ('{"' + "a" * 41 + '": "http://127.0.0.1:8000"}', "names an invalid application"),
        ('{"": "http://127.0.0.1:8000"}', "names an invalid application"),
        (
            '{"alpaca": "http://127.0.0.1:8000", "alpaca": "http://127.0.0.1:9000"}',
            "names the application 'alpaca' twice",
        ),
        ('{"alpaca": 8000}', "must give the application 'alpaca' its loopback origin"),
        ('{"alpaca": ""}', "must give the application 'alpaca' its loopback origin"),
        ('["http://127.0.0.1:8000"]', "must be a JSON object"),
        ("alpaca=http://127.0.0.1:8000", "must be a JSON object"),
    ],
)
def test_an_invalid_name_or_shape_fails_startup_naming_the_setting(monkeypatch, raw, message):
    with pytest.raises(ValidationError) as error:
        _settings(monkeypatch, raw)

    assert "TAU_LOCAL_MCP_APPLICATIONS" in str(error.value)
    assert message in str(error.value)


@pytest.mark.parametrize(
    ("origin", "message"),
    [
        ("http://10.0.0.5:8000", "must name a loopback host"),
        ("http://0.0.0.0:8000", "must name a loopback host"),
        ("http://example.com:8000", "must name a loopback host"),
        ("http://127.0.0.1.nip.io:8000", "must name a loopback host"),
        ("http://192.168.1.20", "must name a loopback host"),
        ("http://127.0.0.1:8000/api", "must be an origin, with no path"),
        ("http://127.0.0.1:8000?debug=1", "without whitespace, query or fragment"),
        ("http://127.0.0.1:8000#mcp", "without whitespace, query or fragment"),
        ("http://user:SYNTHETIC_PASSWORD@127.0.0.1:8000", "must not carry a user name or password"),
        ("http://127.0.0.1:0", "must name a port between 1 and 65535"),
        ("ftp://127.0.0.1:8000", "must use http or https"),
        ("127.0.0.1:8000", "must use http or https"),
    ],
)
def test_a_non_loopback_origin_is_refused_without_repeating_it(monkeypatch, origin, message):
    with pytest.raises(ValidationError) as error:
        _settings(monkeypatch, f'{{"alpaca": "{origin}"}}')

    text = str(error.value)
    assert "TAU_LOCAL_MCP_APPLICATIONS application 'alpaca'" in text
    assert message in text
    assert origin not in text
    assert "SYNTHETIC_PASSWORD" not in text


def test_local_applications_outside_local_mode_fail_startup(monkeypatch):
    monkeypatch.setenv("TAU_LOCAL_MCP_APPLICATIONS", '{"alpaca": "http://127.0.0.1:8000"}')

    with pytest.raises(ValidationError, match="valid only with TAU_LOCAL_MODE=true"):
        TauSDKSettings(_env_file=None, runtime_credential_id="credential-id")


async def test_a_local_application_and_a_release_never_take_each_others_place(monkeypatch):
    settings = _settings(monkeypatch, '{"alpaca": "http://127.0.0.1:8000"}')
    release = MCPApplication(
        name="alpaca", resource_release_uid="6f2c7c52-2b41-4c4e-9c43-36d2e0a8f1aa"
    )

    with pytest.raises(TypeError, match="only local applications"):
        await local_application_connector(settings, AsyncMock())(release)
    with pytest.raises(TypeError, match="only declared applications"):
        await application_connector(settings)(settings.local_mcp_applications[0])


async def test_localhost_must_resolve_only_to_loopback_addresses(monkeypatch):
    local = [LocalMCPApplication(name="alpaca", origin="http://localhost:8000")]
    # This machine's own `localhost` passes.
    await check_local_applications(local)

    loop = asyncio.get_running_loop()

    def resolves_to(*addresses: str):
        async def getaddrinfo(*_args, **_kwargs):
            return [
                (socket.AF_INET6 if ":" in address else socket.AF_INET, 1, 6, "", (address, 0))
                for address in addresses
            ]

        monkeypatch.setattr(loop, "getaddrinfo", getaddrinfo)

    resolves_to("127.0.0.1", "::1")
    await check_local_applications(local)
    resolves_to("127.0.0.1", "10.0.0.5")
    with pytest.raises(ConfigurationError, match="names the application 'alpaca' at localhost"):
        await check_local_applications(local)
    resolves_to()
    with pytest.raises(ConfigurationError, match=r"Name 127\.0\.0\.1 or ::1 instead"):
        await check_local_applications(local)
    # An address is not resolved at all.
    resolves_to("10.0.0.5")
    await check_local_applications(
        [LocalMCPApplication(name="alpaca", origin="http://127.0.0.1:8000")]
    )
