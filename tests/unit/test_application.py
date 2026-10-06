from unittest.mock import AsyncMock, Mock

from ms_tau_sdk.application import ApplicationServices
from ms_tau_sdk.backend.auth import CLITokenAuth, JWTAuth
from ms_tau_sdk.backend.local import LocalDevelopmentBackend
from ms_tau_sdk.cli import run
from ms_tau_sdk.settings import TauSDKSettings


async def test_application_services_own_startup_and_shutdown(tmp_path, runtime_identity_token_file):
    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        runtime_credential_id="credential-id",
        runtime_identity_token_file=runtime_identity_token_file,
    )
    backend = Mock()
    backend.aclose = AsyncMock()
    runtime = Mock()
    runtime.start = AsyncMock()
    runtime.aclose = AsyncMock()
    services = ApplicationServices(
        settings=settings,
        auth=Mock(),
        backend=backend,
        providers=Mock(),
        runtime=runtime,
    )

    await services.start()
    await services.aclose()

    runtime.start.assert_awaited_once_with()
    runtime.aclose.assert_awaited_once_with()
    backend.aclose.assert_awaited_once_with()


def test_cli_runs_constructed_application(monkeypatch, tmp_path):
    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        runtime_credential_id="credential-id",
        host="127.0.0.1",
        port=9876,
    )
    uvicorn_run = Mock()
    monkeypatch.setattr("ms_tau_sdk.cli.uvicorn.run", uvicorn_run)

    run(settings)

    application = uvicorn_run.call_args.args[0]
    assert application.state.settings is settings
    uvicorn_run.assert_called_once_with(
        application,
        host="127.0.0.1",
        port=9876,
        log_config=None,
        access_log=False,
    )


def test_application_builds_local_state_and_remote_service_composite(tmp_path):
    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        local_state_root=tmp_path / "state",
        auth_mode="jwt",
        local_mode=True,
        access_token="access-token",
        refresh_token="refresh-token",
        local_provider="openai",
        local_model="gpt-5.4",
    )

    services = ApplicationServices.create(settings)

    assert isinstance(services.auth, JWTAuth)
    assert isinstance(services.backend, LocalDevelopmentBackend)
    assert services.backend.auth is services.auth
    assert services.runtime.snapshot()["mainsequence_auth_source"] == "environment"


def test_application_asks_the_cli_when_local_mode_has_no_token(tmp_path, monkeypatch):
    for name in ("MAINSEQUENCE_ACCESS_TOKEN", "MAINSEQUENCE_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        local_state_root=tmp_path / "state",
        auth_mode="jwt",
        local_mode=True,
        local_provider="openai",
        local_model="gpt-5.4",
    )

    services = ApplicationServices.create(settings)

    assert isinstance(services.auth, CLITokenAuth)
    assert isinstance(services.backend, LocalDevelopmentBackend)
    assert services.backend.auth is services.auth
    assert services.runtime.snapshot()["mainsequence_auth_source"] == "cli"


def test_application_passes_the_provider_timeout_to_the_factory(tmp_path):
    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        runtime_credential_id="credential-id",
        provider_timeout_seconds=300,
    )

    services = ApplicationServices.create(settings)

    assert services.providers.provider_timeout_seconds == 300
