from unittest.mock import AsyncMock, Mock

from astro.application import ApplicationServices
from astro.cli import run
from astro.settings import Settings


async def test_application_services_own_startup_and_shutdown(tmp_path):
    settings = Settings(
        _env_file=None,
        code_repository_root=tmp_path,
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
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
    settings = Settings(
        _env_file=None,
        code_repository_root=tmp_path,
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
        host="127.0.0.1",
        port=9876,
    )
    uvicorn_run = Mock()
    monkeypatch.setattr("astro.cli.uvicorn.run", uvicorn_run)

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
