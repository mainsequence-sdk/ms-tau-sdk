"""Local-mode authentication through the token command of a Main Sequence CLI.

Every test runs a fake CLI: a small script executed by the current interpreter. No test runs a
real `mainsequence` command, reads a credential store, or uses a real token.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from ms_tau_sdk.app import create_app
from ms_tau_sdk.application import ApplicationServices
from ms_tau_sdk.backend import auth as auth_module
from ms_tau_sdk.backend.auth import CLITokenAuth, JWTAuth, local_mode_auth
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.local import LocalDevelopmentBackend
from ms_tau_sdk.errors import BackendError, ConfigurationError
from ms_tau_sdk.logging import configure_logging
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings

BACKEND = "https://backend.test"
FIRST_TOKEN = "dummy-access-token-first"
SECOND_TOKEN = "dummy-access-token-second"
RECORDED_ENVIRONMENT = (
    "MAINSEQUENCE_ENDPOINT",
    "MAINSEQUENCE_ACCESS_TOKEN",
    "MAINSEQUENCE_REFRESH_TOKEN",
    "TAU_TEST_INHERITED",
)

# Each run appends its arguments and the recorded environment to `calls.jsonl`, then plays the
# step for that run from `steps.json`. The last step repeats.
_FAKE_CLI = """
import json
import os
import sys
import time
from pathlib import Path

here = Path(__file__).resolve().parent
steps = json.loads((here / "steps.json").read_text(encoding="utf-8"))
calls = here / "calls.jsonl"
run = calls.read_text(encoding="utf-8").count("\\n") if calls.exists() else 0
with calls.open("a", encoding="utf-8") as stream:
    recorded = {name: os.environ.get(name) for name in json.loads(sys.argv[1])}
    stream.write(json.dumps({"argv": sys.argv[2:], "environment": recorded}) + "\\n")
step = steps[min(run, len(steps) - 1)]
time.sleep(step.get("sleep", 0))
sys.stderr.write(step.get("stderr", ""))
sys.stdout.write(step.get("stdout", ""))
raise SystemExit(step.get("exit", 0))
"""


class FakeCLI:
    """An executable named `mainsequence` that plays scripted answers."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True)
        self._directory = directory
        script = directory / "fake_mainsequence.py"
        script.write_text(_FAKE_CLI, encoding="utf-8")
        self.path = directory / "mainsequence"
        self.path.write_text(
            "#!/bin/sh\n"
            f"exec {shlex.quote(sys.executable)} {shlex.quote(str(script))} "
            f'{shlex.quote(json.dumps(RECORDED_ENVIRONMENT))} "$@"\n',
            encoding="utf-8",
        )
        self.path.chmod(0o755)
        self.plays(token_answer())

    def plays(self, *steps: dict[str, Any]) -> None:
        (self._directory / "steps.json").write_text(json.dumps(list(steps)), encoding="utf-8")

    @property
    def calls(self) -> list[dict[str, Any]]:
        log = self._directory / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def token_answer(
    token: str = FIRST_TOKEN,
    *,
    endpoint: str = BACKEND,
    expires_in: float | None = 3600,
    token_type: str = "Bearer",
) -> dict[str, Any]:
    """One successful run of `auth token --json`."""
    payload = {
        "endpoint": endpoint,
        "access_token": token,
        "token_type": token_type,
        "expires_at": None if expires_in is None else time.time() + expires_in,
    }
    return {"stdout": json.dumps(payload) + "\n", "stderr": "Using the saved session.\n"}


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's own environment and project `.env` out of these tests."""
    for name in (
        "MAINSEQUENCE_ACCESS_TOKEN",
        "MAINSEQUENCE_REFRESH_TOKEN",
        "MAINSEQUENCE_CLI",
        "MAINSEQUENCE_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def fake_cli(tmp_path: Path) -> FakeCLI:
    return FakeCLI(tmp_path / "cli")


def _settings(tmp_path: Path, cli: Path | None, **overrides: Any) -> TauSDKSettings:
    values: dict[str, Any] = {
        "workspace": tmp_path,
        "local_state_root": tmp_path / "state",
        "backend_url": BACKEND,
        "auth_mode": "jwt",
        "local_mode": True,
        "local_provider": "openai",
        "local_model": "gpt-5.4",
        "mainsequence_cli": cli,
    }
    values.update(overrides)
    return TauSDKSettings(_env_file=None, **values)


def _json_events(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.strip().startswith("{")]


async def test_token_comes_from_the_cli_command_and_is_cached(tmp_path, fake_cli):
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))

    first = await auth.headers()
    second = await auth.headers()

    assert first == second == {"Authorization": f"Bearer {FIRST_TOKEN}"}
    assert [call["argv"] for call in fake_cli.calls] == [["auth", "token", "--json"]]
    # The documented limits: 15 seconds for an answer, reuse until 60 seconds before expiry.
    assert auth_module.CLI_TOKEN_TIMEOUT_SECONDS == 15
    assert auth_module.CLI_TOKEN_REUSE_MARGIN_SECONDS == 60


async def test_event_loop_keeps_running_while_the_command_runs(tmp_path, fake_cli):
    fake_cli.plays({**token_answer(), "sleep": 0.4})
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(tick())
    await auth.headers()
    ticker.cancel()

    # A blocked loop would not tick at all during the 0.4 second command.
    assert ticks >= 10


async def test_prefetch_validates_settings_and_obtains_the_token_once(tmp_path, fake_cli):
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))

    await auth.prefetch()

    assert await auth.headers() == {"Authorization": f"Bearer {FIRST_TOKEN}"}
    assert len(fake_cli.calls) == 1

    incomplete = CLITokenAuth(_settings(tmp_path, fake_cli.path, local_model=None))
    with pytest.raises(ConfigurationError, match="TAU_LOCAL_MODEL"):
        await incomplete.prefetch()
    assert len(fake_cli.calls) == 1


async def test_token_is_renewed_within_sixty_seconds_of_expiry(tmp_path, fake_cli):
    # 45 seconds is inside the 60 second margin. 90 seconds is outside it.
    fake_cli.plays(
        token_answer(FIRST_TOKEN, expires_in=45),
        token_answer(SECOND_TOKEN, expires_in=90),
    )
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))

    assert await auth.headers() == {"Authorization": f"Bearer {FIRST_TOKEN}"}
    assert await auth.headers() == {"Authorization": f"Bearer {SECOND_TOKEN}"}
    assert await auth.headers() == {"Authorization": f"Bearer {SECOND_TOKEN}"}

    assert len(fake_cli.calls) == 2


async def test_token_without_expiry_is_reused_until_a_forced_refresh(tmp_path, fake_cli):
    fake_cli.plays(
        token_answer(FIRST_TOKEN, expires_in=None),
        token_answer(SECOND_TOKEN, expires_in=None),
    )
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))

    assert await auth.headers() == await auth.headers()
    assert len(fake_cli.calls) == 1

    assert await auth.headers(force=True) == {"Authorization": f"Bearer {SECOND_TOKEN}"}
    assert await auth.headers() == {"Authorization": f"Bearer {SECOND_TOKEN}"}
    assert len(fake_cli.calls) == 2


async def test_forced_refresh_runs_the_command_again_once(tmp_path, fake_cli):
    fake_cli.plays(token_answer(FIRST_TOKEN), token_answer(SECOND_TOKEN))
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))

    await auth.headers()
    forced = await auth.headers(force=True)
    after = await auth.headers()

    assert forced == after == {"Authorization": f"Bearer {SECOND_TOKEN}"}
    assert len(fake_cli.calls) == 2


async def test_concurrent_callers_share_one_command_run(tmp_path, fake_cli):
    fake_cli.plays(
        {**token_answer(FIRST_TOKEN), "sleep": 0.2},
        {**token_answer(SECOND_TOKEN), "sleep": 0.2},
    )
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))

    first = await asyncio.gather(*(auth.headers() for _ in range(5)))
    assert first == [{"Authorization": f"Bearer {FIRST_TOKEN}"}] * 5
    assert len(fake_cli.calls) == 1

    # Several requests that were rejected together ask for one new token, not one each.
    forced = await asyncio.gather(*(auth.headers(force=True) for _ in range(3)))
    assert forced == [{"Authorization": f"Bearer {SECOND_TOKEN}"}] * 3
    assert len(fake_cli.calls) == 2


async def test_backend_client_asks_the_cli_again_after_unauthorized(tmp_path, fake_cli):
    fake_cli.plays(token_answer(FIRST_TOKEN), token_answer(SECOND_TOKEN))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json={"ok": True})

    http = httpx.AsyncClient(base_url=BACKEND, transport=httpx.MockTransport(handler))
    settings = _settings(tmp_path, fake_cli.path)
    backend = MainSequenceClient(settings, CLITokenAuth(settings), client=http)

    assert await backend._request("POST", "/resource", json={"value": 1}) == {"ok": True}

    assert seen == [f"Bearer {FIRST_TOKEN}", f"Bearer {SECOND_TOKEN}"]
    assert len(fake_cli.calls) == 2
    await http.aclose()


async def test_child_environment_names_the_backend_and_drops_the_token_pair(
    tmp_path, fake_cli, monkeypatch
):
    settings = _settings(tmp_path, fake_cli.path)
    # A pair left in the process environment must not reach the CLI, and the CLI must answer
    # for Tau's backend whatever the process environment says.
    monkeypatch.setenv("MAINSEQUENCE_ACCESS_TOKEN", "stale-access-token")
    monkeypatch.setenv("MAINSEQUENCE_REFRESH_TOKEN", "stale-refresh-token")
    monkeypatch.setenv("MAINSEQUENCE_ENDPOINT", "https://another-backend.test")
    monkeypatch.setenv("TAU_TEST_INHERITED", "kept")

    await CLITokenAuth(settings).headers()

    assert fake_cli.calls[0]["environment"] == {
        "MAINSEQUENCE_ENDPOINT": BACKEND,
        "MAINSEQUENCE_ACCESS_TOKEN": None,
        "MAINSEQUENCE_REFRESH_TOKEN": None,
        "TAU_TEST_INHERITED": "kept",
    }


async def test_endpoint_is_compared_without_trailing_slashes(tmp_path, fake_cli):
    fake_cli.plays(token_answer(endpoint=f"{BACKEND}/"))
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))

    assert await auth.headers() == {"Authorization": f"Bearer {FIRST_TOKEN}"}


async def test_endpoint_with_an_ipv6_host_and_a_port_is_accepted(tmp_path, fake_cli):
    fake_cli.plays(token_answer(endpoint="http://[::1]:8000"))
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path, backend_url="http://[::1]:8000/"))

    assert await auth.headers() == {"Authorization": f"Bearer {FIRST_TOKEN}"}

    fake_cli.plays(token_answer(endpoint="http://[::1]:9000"))
    with pytest.raises(ConfigurationError, match=r"answered for http://\[::1\]:9000, but Tau"):
        await auth.headers(force=True)


async def test_answer_for_another_backend_is_a_configuration_error(tmp_path, fake_cli):
    fake_cli.plays(token_answer(endpoint="https://user:password@another-backend.test/?q=1"))
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))

    with pytest.raises(ConfigurationError) as raised:
        await auth.headers()

    message = str(raised.value)
    assert "https://another-backend.test" in message
    assert BACKEND in message
    assert "password" not in message
    assert FIRST_TOKEN not in message


@pytest.mark.parametrize(
    ("exit_code", "error_type", "expected"),
    [
        (1, ConfigurationError, "has no usable session for https://backend.test. Run `mainseq"),
        (2, ConfigurationError, "does not know `auth token` (exit code 2). It is too old"),
        (3, ConfigurationError, "found no credential store on this machine"),
        (7, BackendError, "failed `auth token` with exit code 7"),
    ],
)
async def test_each_exit_code_has_its_own_error(
    tmp_path, fake_cli, exit_code, error_type, expected
):
    fake_cli.plays(
        {
            "exit": exit_code,
            "stdout": json.dumps({"access_token": FIRST_TOKEN}),
            "stderr": "\nNot logged in to this backend.\nSecond line.\n",
        }
    )
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))

    with pytest.raises(error_type) as raised:
        await auth.headers()

    message = str(raised.value)
    assert type(raised.value) is error_type
    assert expected in message
    assert str(fake_cli.path) in message
    assert FIRST_TOKEN not in message
    assert "Second line" not in message
    # The usage error of an old CLI says nothing useful, so only the other codes quote it.
    assert ("Not logged in to this backend." in message) is (exit_code != 2)


async def test_command_that_does_not_answer_in_time_is_stopped(tmp_path, fake_cli, monkeypatch):
    monkeypatch.setattr(auth_module, "CLI_TOKEN_TIMEOUT_SECONDS", 0.5)
    fake_cli.plays({**token_answer(), "sleep": 30})
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))
    started_at = time.monotonic()

    with pytest.raises(BackendError, match=r"did not answer `auth token` within 0\.5 seconds"):
        await auth.headers()

    # The command was stopped at the limit; it did not run its 30 seconds.
    assert time.monotonic() - started_at < 5


@pytest.mark.parametrize(
    ("stdout", "problem"),
    [
        (f"token: {FIRST_TOKEN}", "it is not JSON"),
        ("", "it is not JSON"),
        (json.dumps([FIRST_TOKEN]), "it is not a JSON object"),
        (
            json.dumps({"access_token": FIRST_TOKEN, "token_type": "Bearer", "expires_at": None}),
            "`endpoint` is not an HTTP URL",
        ),
        (
            json.dumps(
                {
                    "endpoint": FIRST_TOKEN,
                    "access_token": FIRST_TOKEN,
                    "token_type": "Bearer",
                    "expires_at": None,
                }
            ),
            "`endpoint` is not an HTTP URL",
        ),
        (
            json.dumps(
                {
                    "endpoint": "https://[not-an-address",
                    "access_token": FIRST_TOKEN,
                    "token_type": "Bearer",
                    "expires_at": None,
                }
            ),
            "`endpoint` is not an HTTP URL",
        ),
        (
            json.dumps({"endpoint": BACKEND, "token_type": "Bearer", "expires_at": None}),
            "`access_token` is missing",
        ),
        (
            json.dumps(
                {
                    "endpoint": BACKEND,
                    "access_token": FIRST_TOKEN,
                    "token_type": "MAC",
                    "expires_at": None,
                }
            ),
            "`token_type` is not Bearer",
        ),
        (
            json.dumps({"endpoint": BACKEND, "access_token": FIRST_TOKEN, "token_type": "Bearer"}),
            "`expires_at` is missing",
        ),
        (
            json.dumps(
                {
                    "endpoint": BACKEND,
                    "access_token": FIRST_TOKEN,
                    "token_type": "Bearer",
                    "expires_at": "tomorrow",
                }
            ),
            "`expires_at` is not a number or null",
        ),
    ],
)
async def test_output_that_is_not_the_token_contract_is_rejected(
    tmp_path, fake_cli, stdout, problem
):
    fake_cli.plays({"stdout": stdout})
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))

    with pytest.raises(BackendError) as raised:
        await auth.headers()

    message = str(raised.value)
    assert f"not the documented token contract: {problem}" in message
    assert "Upgrade it" in message
    assert FIRST_TOKEN not in message


async def test_cli_that_cannot_be_started_is_a_configuration_error(tmp_path, fake_cli):
    fake_cli.path.chmod(0o644)
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))

    with pytest.raises(ConfigurationError, match="could not be started"):
        await auth.headers()

    assert fake_cli.calls == []


async def test_missing_cli_names_the_three_ways_out(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(sys, "executable", str(empty / "python"))
    auth = CLITokenAuth(_settings(tmp_path, None))

    with pytest.raises(ConfigurationError) as raised:
        await auth.headers()

    message = str(raised.value)
    assert "`mainsequence login`" in message
    assert "MAINSEQUENCE_CLI" in message
    assert "MAINSEQUENCE_ACCESS_TOKEN and MAINSEQUENCE_REFRESH_TOKEN in the environment" in message


async def test_no_token_reaches_an_error_or_a_log(tmp_path, fake_cli, capsys):
    configure_logging("INFO", machine_sink=True, human_sink=False)
    fake_cli.plays(
        token_answer(FIRST_TOKEN, expires_in=None),
        {"exit": 1, "stdout": token_answer(SECOND_TOKEN)["stdout"], "stderr": "No session.\n"},
        {"stdout": f"{SECOND_TOKEN}\n"},
        token_answer(SECOND_TOKEN, endpoint="https://another-backend.test"),
        {**token_answer(SECOND_TOKEN), "sleep": 30},
    )
    auth = CLITokenAuth(_settings(tmp_path, fake_cli.path))
    messages: list[str] = []

    await auth.headers()
    for _ in range(3):
        with pytest.raises((BackendError, ConfigurationError)) as raised:
            await auth.headers(force=True)
        messages.append(str(raised.value))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(auth_module, "CLI_TOKEN_TIMEOUT_SECONDS", 0.3)
        with pytest.raises(BackendError) as raised:
            await auth.headers(force=True)
        messages.append(str(raised.value))

    captured = capsys.readouterr()
    events = [
        event
        for event in _json_events(captured.out)
        if event["event"] == "local.auth.cli_token.completed"
    ]
    assert [event["outcome"] for event in events] == [
        "success",
        "no_session",
        "malformed_output",
        "endpoint_mismatch",
        "timeout",
    ]
    assert [event["level"] for event in events] == ["info", *["warning"] * 4]
    assert all(isinstance(event["duration_ms"], float) for event in events)
    for secret in (FIRST_TOKEN, SECOND_TOKEN):
        assert secret not in captured.out
        assert secret not in captured.err
        assert all(secret not in message for message in messages)
    # The failed runs kept the token that was already in memory.
    assert await auth.headers() == {"Authorization": f"Bearer {FIRST_TOKEN}"}


async def test_local_mode_selects_the_cli_only_without_a_token_pair(tmp_path, fake_cli):
    assert isinstance(local_mode_auth(_settings(tmp_path, fake_cli.path)), CLITokenAuth)

    with_pair = _settings(
        tmp_path,
        fake_cli.path,
        access_token="dummy-access-token",
        refresh_token="dummy-refresh-token",
    )
    pair_auth = local_mode_auth(with_pair)
    assert isinstance(pair_auth, JWTAuth)
    # With the pair set, the CLI is never run, even when one is found.
    await pair_auth.prefetch()
    assert await pair_auth.headers() == {"Authorization": "Bearer dummy-access-token"}
    assert fake_cli.calls == []

    with_one = _settings(tmp_path, fake_cli.path, access_token="dummy-access-token")
    assert isinstance(local_mode_auth(with_one), JWTAuth)
    with pytest.raises(ConfigurationError, match="MAINSEQUENCE_REFRESH_TOKEN"):
        with_one.validate_runtime_auth()


def test_tokens_read_from_the_env_file_log_one_deprecation_warning(tmp_path, capsys):
    (tmp_path / ".env").write_text(
        "MAINSEQUENCE_ACCESS_TOKEN=dummy-file-access-token\n"
        "MAINSEQUENCE_REFRESH_TOKEN=dummy-file-refresh-token\n",
        encoding="utf-8",
    )
    configure_logging("INFO", machine_sink=True, human_sink=False)
    settings = TauSDKSettings(
        workspace=tmp_path,
        local_state_root=tmp_path / "state",
        auth_mode="jwt",
        local_mode=True,
        local_provider="openai",
        local_model="gpt-5.4",
    )

    assert settings.local_auth_source() == "env_file"
    assert isinstance(local_mode_auth(settings), JWTAuth)

    output = capsys.readouterr().out
    warnings = [
        event
        for event in _json_events(output)
        if event["event"] == "local.auth.env_file_tokens.deprecated"
    ]
    assert len(warnings) == 1
    warning = warnings[0]
    assert warning["level"] == "warning"
    assert Path(warning["env_file"]).samefile(tmp_path / ".env")
    assert warning["variables"] == ["MAINSEQUENCE_ACCESS_TOKEN", "MAINSEQUENCE_REFRESH_TOKEN"]
    assert warning["env_file"] in warning["message"]
    assert "MAINSEQUENCE_ACCESS_TOKEN and MAINSEQUENCE_REFRESH_TOKEN" in warning["message"]
    assert "deprecated" in warning["message"]
    assert "`mainsequence refresh-token`" in warning["message"]
    assert "dummy-file-access-token" not in output
    assert "dummy-file-refresh-token" not in output


def test_tokens_from_the_process_environment_log_no_warning(tmp_path, capsys, monkeypatch):
    # The file holds a pair too, but the process environment is what the settings read.
    (tmp_path / ".env").write_text(
        "MAINSEQUENCE_ACCESS_TOKEN=dummy-file-access-token\n"
        "MAINSEQUENCE_REFRESH_TOKEN=dummy-file-refresh-token\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAINSEQUENCE_ACCESS_TOKEN", "dummy-exported-access-token")
    monkeypatch.setenv("MAINSEQUENCE_REFRESH_TOKEN", "dummy-exported-refresh-token")
    configure_logging("INFO", machine_sink=True, human_sink=False)
    settings = TauSDKSettings(
        workspace=tmp_path,
        local_state_root=tmp_path / "state",
        auth_mode="jwt",
        local_mode=True,
        local_provider="openai",
        local_model="gpt-5.4",
    )

    assert settings.local_auth_source() == "environment"
    assert isinstance(local_mode_auth(settings), JWTAuth)

    events = _json_events(capsys.readouterr().out)
    assert [event for event in events if "deprecated" in str(event["event"])] == []


def test_tokens_passed_to_the_settings_are_not_attributed_to_the_env_file(tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MAINSEQUENCE_ACCESS_TOKEN=dummy-file-access-token\n"
        "MAINSEQUENCE_REFRESH_TOKEN=dummy-file-refresh-token\n",
        encoding="utf-8",
    )
    configure_logging("INFO", machine_sink=True, human_sink=False)
    settings = _settings(
        tmp_path,
        None,
        access_token="dummy-explicit-access-token",
        refresh_token="dummy-explicit-refresh-token",
    )

    assert settings.local_auth_source() == "environment"
    assert isinstance(local_mode_auth(settings), JWTAuth)

    # A project file that cannot be read as settings decides nothing about the source.
    env_file.write_text("MAINSEQUENCE_TAU_TRUSTED_ORIGINS=not,a,json,list\n", encoding="utf-8")
    assert settings.local_auth_source() == "environment"

    events = _json_events(capsys.readouterr().out)
    assert [event for event in events if "deprecated" in str(event["event"])] == []


async def test_local_mode_starts_and_serves_with_a_cli_session_and_no_token(
    tmp_path, fake_cli, asgi_client
):
    settings = _settings(
        tmp_path,
        fake_cli.path,
        state_root=tmp_path / "tau-state",
        exclude_mainsequence_mcp=True,
    )
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers["Authorization"]))
        return httpx.Response(
            200,
            json={
                "credentials": {
                    "openai": {
                        "status": "active",
                        "credential_kind": "api_key",
                        "credential": {"type": "api_key", "api_key": "dummy-provider-key"},
                    }
                },
                "provider_control": {
                    "schema_version": 1,
                    "catalog_digest": f"sha256:{'0' * 64}",
                    "provider": "openai",
                    "model": {
                        "model": "gpt-5.4",
                        "api": "openai-responses",
                        "input": ["text"],
                        "reasoning": False,
                        "thinking_levels": [],
                    },
                },
            },
        )

    http = httpx.AsyncClient(base_url=BACKEND, transport=httpx.MockTransport(handler))
    auth = local_mode_auth(settings)
    backend = LocalDevelopmentBackend(settings, MainSequenceClient(settings, auth, client=http))
    runtime = SessionRuntimeManager(settings=settings, backend=backend, providers=Mock())
    services = ApplicationServices(
        settings=settings,
        auth=auth,
        backend=backend,
        providers=Mock(),
        runtime=runtime,
    )
    app = create_app(settings, services_factory=lambda _settings: services)

    async with asgi_client(app, lifespan=True) as client:
        health = (await client.get("/health")).json()
        ready = await client.get("/ready")
        chat_sessions = await client.get("/api/local/v1/chat-sessions")
    await http.aclose()

    assert isinstance(auth, CLITokenAuth)
    assert health["mode"] == "local"
    assert health["mainsequence_auth_ready"] is True
    assert health["mainsequence_auth_source"] == "cli"
    assert health["provider_control_ready"] is True
    assert ready.status_code == 200
    assert chat_sessions.status_code == 200
    assert chat_sessions.json()["sessions"] == []
    # Startup authenticated the provider request with the CLI token, and one command run
    # served startup, provider hydration and the owner scope of the session list.
    assert seen == [
        ("/api/v1/model-provider-credentials/hydrate/", f"Bearer {FIRST_TOKEN}"),
    ]
    assert len(fake_cli.calls) == 1
    assert FIRST_TOKEN not in json.dumps(health)


async def test_startup_without_a_token_or_a_cli_fails_before_any_network_work(
    tmp_path, monkeypatch
):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(sys, "executable", str(empty / "python"))
    services = ApplicationServices.create(_settings(tmp_path, None))

    assert isinstance(services.auth, CLITokenAuth)
    assert services.backend.auth is services.auth
    with pytest.raises(ConfigurationError, match="no Main Sequence CLI was found"):
        await services.start()

    await services.backend.aclose()
