from pathlib import Path

import pytest

from astro.errors import ConfigurationError
from astro.settings import Settings


def test_settings_normalize_backend_and_origins():
    settings = Settings(
        _env_file=None,
        backend_url="http://backend:8000/",
        trusted_origins="http://one.test,http://two.test",
        code_repository_root=Path("/workspace"),
    )

    assert settings.backend_url == "http://backend:8000"
    assert settings.trusted_origins == ("http://one.test", "http://two.test")


def test_settings_accept_only_code_repository_cwd(monkeypatch):
    monkeypatch.delenv("ASTRO_CODE_REPOSITORY_CWD", raising=False)
    settings = Settings(_env_file=None)
    assert settings.code_repository_root == Path("/workspace")

    monkeypatch.setenv("ASTRO_CODE_REPOSITORY_CWD", "/canonical")
    settings = Settings(_env_file=None)
    assert settings.code_repository_root == Path("/canonical")


def test_runtime_auth_requires_both_credential_parts():
    settings = Settings(
        _env_file=None,
        runtime_credential_id="credential-id",
        runtime_credential_secret=None,
    )

    with pytest.raises(ConfigurationError, match="MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET"):
        settings.validate_runtime_auth()


def test_settings_reject_invalid_lease_and_logging_contracts():
    with pytest.raises(
        ValueError,
        match="ASTRO_SESSION_LEASE_RENEW_SECONDS",
    ):
        Settings(
            _env_file=None,
            runtime_lease_ttl_seconds=30,
            runtime_lease_renew_interval_seconds=30,
        )

    with pytest.raises(ValueError, match="logging sink"):
        Settings(
            _env_file=None,
            log_machine_sink=False,
            log_human_sink=False,
        )
