import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from ms_tau_sdk.errors import ConfigurationError
from ms_tau_sdk.resources.loader import resource_root
from ms_tau_sdk.settings import TauSDKSettings, find_mainsequence_cli


def test_settings_normalize_backend_and_origins(tmp_path):
    settings = TauSDKSettings(
        _env_file=None,
        backend_url="http://backend:8000/",
        trusted_origins="http://one.test,http://two.test",
        workspace=tmp_path,
    )

    assert settings.backend_url == "http://backend:8000"
    assert settings.trusted_origins == ("http://one.test", "http://two.test")


def test_tool_exclusion_settings_default_and_environment(monkeypatch, tmp_path):
    defaults = TauSDKSettings(_env_file=None, workspace=tmp_path)
    assert defaults.exclude_base_tools is False
    assert defaults.exclude_mainsequence_mcp is False

    monkeypatch.setenv("TAU_EXCLUDE_BASE_TOOLS", "true")
    monkeypatch.setenv("TAU_EXCLUDE_MAINSEQUENCE_MCP", "1")
    configured = TauSDKSettings(_env_file=None, workspace=tmp_path)
    assert configured.exclude_base_tools is True
    assert configured.exclude_mainsequence_mcp is True

    explicit = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        exclude_base_tools=False,
        exclude_mainsequence_mcp=False,
    )
    assert explicit.exclude_base_tools is False
    assert explicit.exclude_mainsequence_mcp is False


def test_a2a_terminalization_and_local_recovery_settings(monkeypatch, tmp_path):
    defaults = TauSDKSettings(_env_file=None, workspace=tmp_path)
    assert defaults.a2a_task_wait_timeout_seconds == 30
    assert defaults.local_a2a_task_reconcile_interval_seconds == 5
    assert defaults.local_a2a_task_stale_after_seconds == 120
    assert defaults.local_a2a_task_pending_timeout_seconds == 300
    assert defaults.local_a2a_task_max_recovery_attempts == 3

    monkeypatch.setenv("MAINSEQUENCE_TAU_A2A_TASK_WAIT_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("TAU_LOCAL_A2A_TASK_RECONCILE_INTERVAL_SECONDS", "2")
    monkeypatch.setenv("TAU_LOCAL_A2A_TASK_STALE_AFTER_SECONDS", "45")
    monkeypatch.setenv("TAU_LOCAL_A2A_TASK_PENDING_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("TAU_LOCAL_A2A_TASK_MAX_RECOVERY_ATTEMPTS", "4")
    configured = TauSDKSettings(_env_file=None, workspace=tmp_path)

    assert configured.a2a_task_wait_timeout_seconds == 12
    assert configured.local_a2a_task_reconcile_interval_seconds == 2
    assert configured.local_a2a_task_stale_after_seconds == 45
    assert configured.local_a2a_task_pending_timeout_seconds == 90
    assert configured.local_a2a_task_max_recovery_attempts == 4


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


def test_tau_state_home_is_workspace_scoped_and_outside_the_package(tmp_path, monkeypatch):
    monkeypatch.delenv("MAINSEQUENCE_TAU_STATE_ROOT", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))

    settings = TauSDKSettings(
        _env_file=None,
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
        workspace=tmp_path,
    )

    assert settings.state_root == tmp_path / "xdg" / "ms-tau-sdk"
    assert settings.tau_state_home == (tmp_path / "xdg" / "ms-tau-sdk" / settings.workspace_digest)
    # The packaged resources are a read-only input; runtime state never lands there.
    assert resource_root() not in settings.tau_state_home.parents


def test_the_state_root_is_explicitly_overridable(tmp_path, monkeypatch):
    monkeypatch.setenv("MAINSEQUENCE_TAU_STATE_ROOT", str(tmp_path / "from-env"))

    from_env = TauSDKSettings(
        _env_file=None,
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
        workspace=tmp_path,
    )
    explicit = TauSDKSettings(
        _env_file=None,
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
        workspace=tmp_path,
        state_root=tmp_path / "explicit",
    )

    assert from_env.state_root == (tmp_path / "from-env").resolve()
    assert explicit.state_root == (tmp_path / "explicit").resolve()


def test_provider_timeout_defaults_to_sixty_seconds_and_reads_the_environment(
    monkeypatch, tmp_path
):
    assert TauSDKSettings(_env_file=None, workspace=tmp_path).provider_timeout_seconds == 60

    monkeypatch.setenv("MAINSEQUENCE_TAU_PROVIDER_TIMEOUT_SECONDS", "300")
    assert TauSDKSettings(_env_file=None, workspace=tmp_path).provider_timeout_seconds == 300

    monkeypatch.setenv("MAINSEQUENCE_TAU_PROVIDER_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValidationError):
        TauSDKSettings(_env_file=None, workspace=tmp_path)


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def machine_without_credentials(monkeypatch, tmp_path) -> Path:
    """Hide the Main Sequence CLI, tokens and project `.env` of the machine running the tests.

    Returns the directory that stands for the running interpreter's directory.
    """
    interpreter_directory = tmp_path / "interpreter"
    interpreter_directory.mkdir()
    (tmp_path / "path").mkdir()
    monkeypatch.setattr(sys, "executable", str(interpreter_directory / "python"))
    monkeypatch.setenv("PATH", str(tmp_path / "path"))
    for name in ("MAINSEQUENCE_CLI", "MAINSEQUENCE_ACCESS_TOKEN", "MAINSEQUENCE_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return interpreter_directory


def _local_settings(tmp_path: Path, **overrides: object) -> TauSDKSettings:
    values: dict[str, object] = {
        "workspace": tmp_path,
        "auth_mode": "jwt",
        "local_mode": True,
        "local_provider": "openai",
        "local_model": "gpt-5.4",
    }
    values.update(overrides)
    return TauSDKSettings(_env_file=None, **values)


def test_mainsequence_cli_is_found_in_the_documented_order(
    machine_without_credentials, monkeypatch, tmp_path
):
    assert find_mainsequence_cli() is None

    on_path = _executable(tmp_path / "path" / "mainsequence")
    assert find_mainsequence_cli() == on_path

    # A file that cannot be executed is not a CLI, so the search goes on to PATH.
    beside_interpreter = machine_without_credentials / "mainsequence"
    beside_interpreter.write_text("not executable", encoding="utf-8")
    assert find_mainsequence_cli() == on_path

    _executable(beside_interpreter)
    assert find_mainsequence_cli() == beside_interpreter

    explicit = _executable(tmp_path / "chosen" / "ms-cli")
    assert find_mainsequence_cli(explicit) == explicit

    # An explicit path is a decision. A wrong one is reported, not replaced by another CLI.
    with pytest.raises(ConfigurationError, match=r"MAINSEQUENCE_CLI is set to .*missing-cli"):
        find_mainsequence_cli(tmp_path / "missing-cli")
    with pytest.raises(ConfigurationError, match="which is not an existing file"):
        find_mainsequence_cli(tmp_path / "chosen")


def test_mainsequence_cli_setting_reads_the_environment(
    machine_without_credentials, monkeypatch, tmp_path
):
    assert _local_settings(tmp_path).mainsequence_cli is None

    monkeypatch.setenv("MAINSEQUENCE_CLI", "   ")
    assert _local_settings(tmp_path).mainsequence_cli is None

    explicit = _executable(tmp_path / "chosen" / "ms-cli")
    monkeypatch.setenv("MAINSEQUENCE_CLI", str(explicit))
    settings = _local_settings(tmp_path)
    assert settings.mainsequence_cli == explicit
    assert settings.mainsequence_cli_path() == explicit


def test_local_mode_with_both_tokens_needs_no_cli(machine_without_credentials, tmp_path):
    settings = _local_settings(
        tmp_path, access_token="dummy-access-token", refresh_token="dummy-refresh-token"
    )

    settings.validate_runtime_auth()

    assert settings.uses_cli_session() is False
    assert settings.local_auth_source() == "environment"


def test_local_mode_with_no_token_needs_a_cli(machine_without_credentials, tmp_path):
    settings = _local_settings(tmp_path)
    assert settings.uses_cli_session() is True
    assert settings.local_auth_source() == "cli"

    with pytest.raises(ConfigurationError) as missing:
        settings.validate_runtime_auth()

    message = str(missing.value)
    assert "MAINSEQUENCE_ACCESS_TOKEN and MAINSEQUENCE_REFRESH_TOKEN are not set" in message
    assert "no Main Sequence CLI was found" in message
    # The three ways out: log in with a CLI, name a CLI, or provide the token pair.
    assert "`mainsequence auth token`" in message
    assert "`mainsequence login`" in message
    assert "set MAINSEQUENCE_CLI to the path of such a CLI" in message
    assert "provide MAINSEQUENCE_ACCESS_TOKEN and MAINSEQUENCE_REFRESH_TOKEN" in message
    assert "Missing local mode settings" not in message

    _executable(machine_without_credentials / "mainsequence")
    settings.validate_runtime_auth()

    # Blank values are not a token pair either.
    blank = _local_settings(tmp_path, access_token="  ", refresh_token="")
    assert blank.local_auth_source() == "cli"
    blank.validate_runtime_auth()


def test_local_mode_with_no_token_reports_a_wrong_explicit_cli(
    machine_without_credentials, tmp_path
):
    _executable(machine_without_credentials / "mainsequence")
    settings = _local_settings(tmp_path, mainsequence_cli=tmp_path / "missing-cli")

    with pytest.raises(ConfigurationError, match=r"MAINSEQUENCE_CLI is set to .*missing-cli"):
        settings.validate_runtime_auth()


@pytest.mark.parametrize(
    ("configured", "missing"),
    [
        ({"access_token": "dummy-access-token"}, "MAINSEQUENCE_REFRESH_TOKEN"),
        ({"refresh_token": "dummy-refresh-token"}, "MAINSEQUENCE_ACCESS_TOKEN"),
    ],
)
def test_local_mode_with_one_token_reports_the_missing_one(
    machine_without_credentials, tmp_path, configured, missing
):
    # A CLI on the machine does not complete half a pair.
    _executable(machine_without_credentials / "mainsequence")
    settings = _local_settings(tmp_path, **configured)

    with pytest.raises(ConfigurationError) as incomplete:
        settings.validate_runtime_auth()

    assert str(incomplete.value) == f"Missing local mode settings: {missing}"
    assert settings.uses_cli_session() is False


def test_local_mode_reports_a_missing_selection_before_the_cli(
    machine_without_credentials, tmp_path
):
    settings = _local_settings(tmp_path, local_provider=None, local_model=None)

    with pytest.raises(ConfigurationError) as missing:
        settings.validate_runtime_auth()

    assert str(missing.value) == "Missing local mode settings: TAU_LOCAL_PROVIDER, TAU_LOCAL_MODEL"


def test_managed_mode_ignores_the_cli_and_the_local_token_pair(
    machine_without_credentials, tmp_path
):
    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
        mainsequence_cli=tmp_path / "missing-cli",
    )

    settings.validate_runtime_auth()
