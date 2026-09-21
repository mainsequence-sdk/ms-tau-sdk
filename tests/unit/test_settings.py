import pytest

from ms_tau_sdk.errors import ConfigurationError
from ms_tau_sdk.settings import TauSDKSettings


def test_settings_normalize_backend_and_origins(tmp_path):
    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend:8000/",
        trusted_origins="http://one.test,http://two.test",
        workspace=tmp_path,
    )

    assert settings.backend_url == "http://backend:8000"
    assert settings.trusted_origins == ("http://one.test", "http://two.test")


def test_settings_use_canonical_mainsequence_endpoint(monkeypatch):
    monkeypatch.setenv("MAINSEQUENCE_ENDPOINT", "https://development.example.test/")

    settings = TauSDKSettings(_env_file=None)

    assert settings.backend_url == "https://development.example.test"


def test_settings_default_to_current_workspace_and_accept_explicit_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAINSEQUENCE_TAU_WORKSPACE", raising=False)
    settings = TauSDKSettings(_env_file=None)
    assert settings.workspace == tmp_path.resolve()

    explicit = tmp_path / "explicit"
    explicit.mkdir()
    monkeypatch.setenv("MAINSEQUENCE_TAU_WORKSPACE", str(explicit))
    settings = TauSDKSettings(_env_file=None)
    assert settings.workspace == explicit.resolve()


def test_settings_reject_missing_or_non_directory_workspace(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="Workspace does not exist"):
        TauSDKSettings(_env_file=None, workspace=missing)

    regular_file = tmp_path / "file.txt"
    regular_file.write_text("not a workspace", encoding="utf-8")
    with pytest.raises(ValueError, match="Workspace is not a directory"):
        TauSDKSettings(_env_file=None, workspace=regular_file)


def test_runtime_auth_requires_both_credential_parts():
    settings = TauSDKSettings(
        _env_file=None,
        runtime_credential_id="credential-id",
        runtime_credential_secret=None,
    )

    with pytest.raises(ConfigurationError, match="MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET"):
        settings.validate_runtime_auth()


def test_settings_reject_invalid_lease_and_logging_contracts():
    with pytest.raises(
        ValueError,
        match="MAINSEQUENCE_TAU_SESSION_LEASE_RENEW_SECONDS",
    ):
        TauSDKSettings(
            _env_file=None,
            runtime_lease_ttl_seconds=30,
            runtime_lease_renew_interval_seconds=30,
        )

    with pytest.raises(ValueError, match="logging sink"):
        TauSDKSettings(
            _env_file=None,
            log_machine_sink=False,
            log_human_sink=False,
        )


def test_local_mode_requires_jwt_auth_and_explicit_selection(tmp_path):
    with pytest.raises(ValueError, match="MAINSEQUENCE_AUTH_MODE=jwt"):
        TauSDKSettings(
            _env_file=None,
            workspace=tmp_path,
            local_mode=True,
        )

    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        auth_mode="jwt",
        local_mode=True,
        access_token="access-token",
        refresh_token="refresh-token",
    )
    with pytest.raises(ConfigurationError, match="TAU_LOCAL_PROVIDER, TAU_LOCAL_MODEL"):
        settings.validate_runtime_auth()


def test_local_mode_is_workspace_scoped_and_loopback_by_default(tmp_path):
    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        auth_mode="jwt",
        local_mode=True,
        access_token="access-token",
        refresh_token="refresh-token",
        local_provider=" openai ",
        local_model=" gpt-5.4 ",
        local_state_root=tmp_path / "state",
    )

    settings.validate_runtime_auth()

    assert settings.host == "127.0.0.1"
    assert settings.local_provider == "openai"
    assert settings.local_model == "gpt-5.4"
    assert settings.local_state_path.parent == (tmp_path / "state" / settings.workspace_digest)
    assert settings.local_log_path == (
        tmp_path / "state" / settings.workspace_digest / "logs" / "tau.jsonl"
    )
    assert settings.local_session_uid(None) == (f"local-{settings.workspace_digest}-default")
    assert settings.local_session_uid("demo") == settings.local_session_uid("demo")
    assert settings.local_session_uid("demo") != settings.local_session_uid("other")

    external = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        auth_mode="jwt",
        local_mode=True,
        access_token="access-token",
        refresh_token="refresh-token",
        local_provider="openai",
        local_model="gpt-5.4",
        host="0.0.0.0",
    )
    assert external.host == "0.0.0.0"
    assert external.loopback_bind is False


def test_managed_mode_rejects_user_jwt_auth():
    with pytest.raises(ValueError, match="supported only when TAU_LOCAL_MODE=true"):
        TauSDKSettings(_env_file=None, auth_mode="jwt")
