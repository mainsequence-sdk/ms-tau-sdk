"""Managed runtime credential exchange with the bootstrap secret or the workload identity token.

Every exchange is answered by a stand-in transport. No test reaches a backend, reads a real token,
or uses the configuration of the machine that runs it.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from ms_tau_sdk.app import create_app
from ms_tau_sdk.application import ApplicationServices
from ms_tau_sdk.backend.auth import RuntimeCredentialAuth
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.errors import BackendError, ConfigurationError
from ms_tau_sdk.logging import configure_logging
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings

BACKEND = "https://backend.test"
EXCHANGE_PATH = "/api/v1/runtime-credentials/token/"
CREDENTIAL_ID = "resource_release_11111111_revision_22222222"
SECRET = "dummy-bootstrap-secret"
SECRET_ENV = "MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET"
TOKEN_FILE_ENV = "MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE"

type Answer = tuple[int, dict[str, str]]
GRANTED: Answer = (200, {})
REJECTED: Answer = (401, {"WWW-Authenticate": 'Bearer realm="runtime-credential-exchange"'})
UNREACHABLE: Answer = (0, {})  # the connection fails and there is no answer


def projected_token(number: int) -> str:
    """A stand-in for a projected ServiceAccount token: distinctive and never a real one."""
    return f"eyJhbGciOiJSUzI1NiJ9.dummy-projected-workload-identity-{number}.signature"


class StandInExchange:
    """Answer the exchange from a script and record every request body. The last answer repeats.

    ``on_request`` runs after a body is recorded, for example to rotate the token file.
    """

    def __init__(self, *answers: Answer) -> None:
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []
        self.expires_in = 900
        self.on_request: Callable[[], object] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", EXCHANGE_PATH)
        self.bodies.append(json.loads(request.content))
        if self.on_request is not None:
            self.on_request()
        status, headers = self.answers[min(len(self.bodies), len(self.answers)) - 1]
        if status == 0:
            raise httpx.ConnectError("All connection attempts failed", request=request)
        if status != 200:
            return httpx.Response(status, headers=headers, json={"detail": "Not accepted."})
        return httpx.Response(
            200,
            json={
                "access": f"dummy-access-{len(self.bodies)}",
                "token_type": "Bearer",
                "expires_in": self.expires_in,
                "runtime_code_repository_context": None,
            },
        )


@pytest.fixture(autouse=True)
def isolated_runtime_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the runtime credential settings of the machine that runs the tests out of them."""
    for name in (
        "MAINSEQUENCE_AUTH_MODE",
        "MAINSEQUENCE_ENDPOINT",
        "MAINSEQUENCE_RUNTIME_CREDENTIAL_ID",
        SECRET_ENV,
        TOKEN_FILE_ENV,
        "TAU_LOCAL_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Record the waits between attempts instead of sleeping through them."""
    sleep = AsyncMock()
    monkeypatch.setattr("ms_tau_sdk.backend.auth.asyncio.sleep", sleep)
    return sleep


@pytest.fixture
async def connect() -> AsyncIterator[
    Callable[[TauSDKSettings, StandInExchange], RuntimeCredentialAuth]
]:
    clients: list[httpx.AsyncClient] = []

    def connected(settings: TauSDKSettings, exchange: StandInExchange) -> RuntimeCredentialAuth:
        client = httpx.AsyncClient(transport=httpx.MockTransport(exchange))
        clients.append(client)
        return RuntimeCredentialAuth(settings, exchange_client=client)

    yield connected
    for client in clients:
        await client.aclose()


def managed_settings(tmp_path: Path, **overrides: Any) -> TauSDKSettings:
    values: dict[str, Any] = {
        "workspace": tmp_path,
        "backend_url": BACKEND,
        "runtime_credential_id": CREDENTIAL_ID,
    }
    values.update(overrides)
    return TauSDKSettings(_env_file=None, **values)


def managed_services(
    settings: TauSDKSettings, exchange: StandInExchange
) -> tuple[ApplicationServices, httpx.AsyncClient]:
    """The managed service graph, with the backend answered by the stand-in exchange."""
    http = httpx.AsyncClient(base_url=BACKEND, transport=httpx.MockTransport(exchange))
    auth = RuntimeCredentialAuth(settings)
    backend = MainSequenceClient(settings, auth, client=http)
    runtime = SessionRuntimeManager(settings=settings, backend=backend, providers=Mock())
    services = ApplicationServices(
        settings=settings, auth=auth, backend=backend, providers=Mock(), runtime=runtime
    )
    return services, http


def project(volume: Path, token: str) -> Path:
    """Write the token the way the kubelet projects it: a new directory behind a swapped link."""
    volume.mkdir(exist_ok=True)
    generation = volume / f"..generation-{len(list(volume.glob('..generation-*')))}"
    generation.mkdir()
    (generation / "token").write_text(token, encoding="utf-8")
    swap = volume / "..data_tmp"
    swap.symlink_to(generation.name)
    swap.replace(volume / "..data")
    link = volume / "token"
    if not link.is_symlink():
        link.symlink_to("..data/token")
    return link


def json_events(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.strip().startswith("{")]


def exception_chain(error: BaseException) -> list[BaseException]:
    links: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in links:
        links.append(current)
        current = current.__cause__ or current.__context__
    return links


async def test_without_a_token_file_the_exchange_sends_the_secret(tmp_path, connect):
    exchange = StandInExchange(GRANTED)
    auth = connect(managed_settings(tmp_path, runtime_credential_secret=SECRET), exchange)

    assert await auth.headers() == {"Authorization": "Bearer dummy-access-1"}
    assert exchange.bodies == [{"credential_id": CREDENTIAL_ID, "credential_secret": SECRET}]


async def test_with_a_token_file_the_exchange_sends_the_token_and_never_the_secret(
    tmp_path, connect
):
    token_file = tmp_path / "token"
    token_file.write_text(projected_token(1) + "\n", encoding="utf-8")
    exchange = StandInExchange(GRANTED)
    # A secret is configured as well. The token file decides, and the secret is never sent.
    settings = managed_settings(
        tmp_path, runtime_identity_token_file=token_file, runtime_credential_secret=SECRET
    )
    auth = connect(settings, exchange)

    assert await auth.headers() == {"Authorization": "Bearer dummy-access-1"}
    assert exchange.bodies == [
        {"credential_id": CREDENTIAL_ID, "workload_identity_token": projected_token(1)}
    ]


async def test_the_deployed_environment_needs_no_secret(tmp_path, monkeypatch, connect):
    token_file = project(tmp_path / "runtime-identity", projected_token(1))
    monkeypatch.setenv("MAINSEQUENCE_AUTH_MODE", "runtime_credential")
    monkeypatch.setenv("MAINSEQUENCE_RUNTIME_CREDENTIAL_ID", CREDENTIAL_ID)
    monkeypatch.setenv(TOKEN_FILE_ENV, str(token_file))
    settings = TauSDKSettings(_env_file=None, workspace=tmp_path, backend_url=BACKEND)
    exchange = StandInExchange(GRANTED)

    settings.validate_runtime_auth()
    await connect(settings, exchange).prefetch()

    assert settings.runtime_credential_secret is None
    assert settings.runtime_identity_token_file == token_file
    assert exchange.bodies == [
        {"credential_id": CREDENTIAL_ID, "workload_identity_token": projected_token(1)}
    ]


async def test_the_token_file_is_read_for_every_exchange_and_not_at_start_up(tmp_path, connect):
    volume = tmp_path / "runtime-identity"
    exchange = StandInExchange(GRANTED)
    # Nothing is projected yet: building the settings and the auth reads no file.
    auth = connect(
        managed_settings(tmp_path, runtime_identity_token_file=volume / "token"), exchange
    )

    project(volume, projected_token(1))
    exchange.expires_in = 1  # the access token is due for renewal at once
    await auth.headers()
    project(volume, projected_token(2))
    exchange.expires_in = 900
    await auth.headers()  # renews the expiring access token
    project(volume, projected_token(3))
    await auth.headers()  # the access token is still valid, so there is no exchange
    await auth.headers(force=True)  # a rejected access token forces a new exchange

    assert [body["workload_identity_token"] for body in exchange.bodies] == [
        projected_token(1),
        projected_token(2),
        projected_token(3),
    ]


@pytest.mark.parametrize(
    ("prepare", "reason"),
    [
        (lambda path: None, "cannot be read (No such file or directory)"),
        (lambda path: path.mkdir(), "cannot be read (Is a directory)"),
        (lambda path: path.write_bytes(b""), "is empty"),
        (lambda path: path.write_bytes(b" \n\t\n"), "is empty"),
        (
            lambda path: path.write_bytes(projected_token(1).encode() * 2000),
            "is larger than 64 KiB, too large for a token",
        ),
        (
            lambda path: path.write_bytes(b"\xff" + projected_token(1).encode()),
            "does not hold text",
        ),
    ],
    ids=["missing", "directory", "empty", "blank", "too-large", "not-text"],
)
async def test_an_unusable_token_file_fails_without_falling_back_to_the_secret(
    tmp_path, connect, prepare, reason
):
    token_file = tmp_path / "token"
    prepare(token_file)
    exchange = StandInExchange(GRANTED)
    settings = managed_settings(
        tmp_path, runtime_identity_token_file=token_file, runtime_credential_secret=SECRET
    )
    auth = connect(settings, exchange)

    with pytest.raises(ConfigurationError) as failure:
        await auth.headers()

    assert str(failure.value) == (
        f"{TOKEN_FILE_ENV} names {token_file}, which {reason}. The runtime credential exchange "
        f"does not fall back to {SECRET_ENV}."
    )
    # Nothing was sent: neither the secret nor anything read from the file.
    assert exchange.bodies == []
    for link in exception_chain(failure.value):
        assert projected_token(1) not in repr(link)
        assert SECRET not in repr(link)


async def test_throttled_and_unavailable_exchanges_wait_as_long_as_retry_after_asks(
    tmp_path, connect, no_wait
):
    token_file = tmp_path / "token"
    token_file.write_text(projected_token(1), encoding="utf-8")
    exchange = StandInExchange((503, {"Retry-After": "7"}), (429, {"Retry-After": "2"}), GRANTED)
    rotations = iter((projected_token(2), projected_token(3)))
    exchange.on_request = lambda: token_file.write_text(next(rotations, ""), encoding="utf-8")
    auth = connect(managed_settings(tmp_path, runtime_identity_token_file=token_file), exchange)

    assert await auth.headers() == {"Authorization": "Bearer dummy-access-3"}

    assert [call.args for call in no_wait.await_args_list] == [(7.0,), (2.0,)]
    # Every attempt read the rotated file again.
    assert [body["workload_identity_token"] for body in exchange.bodies] == [
        projected_token(1),
        projected_token(2),
        projected_token(3),
    ]


@pytest.mark.parametrize("retry_after", [None, "soon"], ids=["no-header", "unusable-header"])
@pytest.mark.parametrize("status", [429, 503])
async def test_retries_back_off_without_a_usable_retry_after_and_are_bounded(
    tmp_path, connect, no_wait, status, retry_after
):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    exchange = StandInExchange((status, headers))
    auth = connect(managed_settings(tmp_path, runtime_credential_secret=SECRET), exchange)

    with pytest.raises(BackendError) as failure:
        await auth.headers()

    assert failure.value.backend_status == status
    assert str(failure.value).startswith(
        f"Runtime credential exchange failed (HTTP {status}) after 4 attempts: "
    )
    assert len(exchange.bodies) == 4
    assert [call.args for call in no_wait.await_args_list] == [(1.0,), (2.0,), (4.0,)]


@pytest.mark.parametrize("zone", ["GMT", "-0000"])
async def test_retry_after_may_be_an_http_date(tmp_path, connect, no_wait, zone):
    later = datetime.now(UTC) + timedelta(seconds=30)
    if zone == "GMT":
        retry_after = format_datetime(later, usegmt=True)
    else:  # a date without a zone is read as UTC
        retry_after = format_datetime(later.replace(tzinfo=None))
    assert retry_after.endswith(zone)
    exchange = StandInExchange((503, {"Retry-After": retry_after}), GRANTED)
    auth = connect(managed_settings(tmp_path, runtime_credential_secret=SECRET), exchange)

    await auth.headers()

    (delay,) = no_wait.await_args.args
    assert 25 < delay <= 30
    assert len(exchange.bodies) == 2


async def test_a_retry_after_beyond_the_bound_fails_without_waiting(tmp_path, connect, no_wait):
    exchange = StandInExchange((503, {"Retry-After": "3600"}))
    auth = connect(managed_settings(tmp_path, runtime_credential_secret=SECRET), exchange)

    with pytest.raises(BackendError) as failure:
        await auth.headers()

    assert failure.value.backend_status == 503
    assert "asked to retry after 3600 seconds, longer than the 60 seconds the SDK waits" in str(
        failure.value
    )
    assert len(exchange.bodies) == 1
    no_wait.assert_not_awaited()


@pytest.mark.parametrize("status", [400, 500])
async def test_other_failed_exchanges_are_not_retried(tmp_path, connect, no_wait, status):
    token_file = tmp_path / "token"
    token_file.write_text(projected_token(1), encoding="utf-8")
    exchange = StandInExchange((status, {"Retry-After": "1"}), GRANTED)
    auth = connect(managed_settings(tmp_path, runtime_identity_token_file=token_file), exchange)

    with pytest.raises(BackendError) as failure:
        await auth.headers()

    assert failure.value.backend_status == status
    assert str(failure.value) == f"Runtime credential exchange failed (HTTP {status})"
    assert len(exchange.bodies) == 1
    no_wait.assert_not_awaited()


@pytest.mark.parametrize("proof", ["workload_identity_token", "credential_secret"])
async def test_a_rejected_exchange_fails_at_once_without_another_proof(
    tmp_path, connect, no_wait, proof
):
    token_file = tmp_path / "token"
    token_file.write_text(projected_token(1), encoding="utf-8")
    overrides: dict[str, Any] = {"runtime_credential_secret": SECRET}
    if proof == "workload_identity_token":
        overrides["runtime_identity_token_file"] = token_file
    exchange = StandInExchange(REJECTED, GRANTED)
    auth = connect(managed_settings(tmp_path, **overrides), exchange)

    with pytest.raises(BackendError) as failure:
        await auth.headers()

    assert failure.value.backend_status == 401
    assert str(failure.value).startswith("Runtime credential exchange was rejected (HTTP 401)")
    assert [list(body) for body in exchange.bodies] == [["credential_id", proof]]
    no_wait.assert_not_awaited()


async def test_a_backend_call_does_not_repeat_a_rejected_exchange(tmp_path, no_wait):
    token_file = tmp_path / "token"
    token_file.write_text(projected_token(1), encoding="utf-8")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == EXCHANGE_PATH:
            return httpx.Response(401, headers=REJECTED[1], json={"detail": "Not accepted."})
        return httpx.Response(200, json={})

    settings = managed_settings(tmp_path, runtime_identity_token_file=token_file)
    async with httpx.AsyncClient(base_url=BACKEND, transport=httpx.MockTransport(handler)) as http:
        backend = MainSequenceClient(settings, RuntimeCredentialAuth(settings), client=http)
        with pytest.raises(BackendError, match=r"rejected \(HTTP 401\)"):
            await backend._request("GET", "/api/v1/agent-sessions/session-1/", idempotent=True)

    assert calls == [EXCHANGE_PATH]
    no_wait.assert_not_awaited()


async def test_the_workload_identity_token_never_leaves_the_exchange(
    tmp_path, connect, no_wait, capsys
):
    configure_logging("DEBUG", machine_sink=True, human_sink=True)
    token_file = tmp_path / "token"
    token_file.write_text(projected_token(1), encoding="utf-8")
    settings = managed_settings(tmp_path, runtime_identity_token_file=token_file)
    exchange = StandInExchange(
        (503, {"Retry-After": "1"}), GRANTED, REJECTED, UNREACHABLE, (503, {})
    )
    auth = connect(settings, exchange)
    errors: list[BaseException] = []

    headers = await auth.headers()  # one retry, then the access token
    token_file.write_text(projected_token(2), encoding="utf-8")
    with pytest.raises(BackendError) as rejected:
        await auth.headers(force=True)
    errors.append(rejected.value)
    with pytest.raises(httpx.ConnectError) as unreachable:
        await auth.headers(force=True)
    errors.append(unreachable.value)
    with pytest.raises(BackendError) as unavailable:
        await auth.headers(force=True)
    errors.append(unavailable.value)
    token_file.write_bytes(projected_token(3).encode() * 2000)
    with pytest.raises(ConfigurationError) as too_large:
        await auth.headers(force=True)
    errors.append(too_large.value)
    token_file.write_bytes(b"\xff" + projected_token(4).encode())
    with pytest.raises(ConfigurationError) as not_text:
        await auth.headers(force=True)
    errors.append(not_text.value)

    captured = capsys.readouterr()
    exchange_events = [
        (event["event"], event.get("outcome"), event.get("status_code"), event.get("error_type"))
        for event in json_events(captured.out)
        if str(event.get("event", "")).startswith("runtime.auth.exchange.")
    ]
    assert exchange_events == [
        ("runtime.auth.exchange.retrying", None, 503, None),
        ("runtime.auth.exchange.completed", "success", None, None),
        ("runtime.auth.exchange.completed", "failed", 401, "BackendError"),
        ("runtime.auth.exchange.completed", "failed", None, "ConnectError"),
        *[("runtime.auth.exchange.retrying", None, 503, None)] * 3,
        ("runtime.auth.exchange.completed", "failed", 503, "BackendError"),
        ("runtime.auth.exchange.completed", "failed", None, "ConfigurationError"),
        ("runtime.auth.exchange.completed", "failed", None, "ConfigurationError"),
    ]
    assert {
        event["proof"]
        for event in json_events(captured.out)
        if str(event.get("event", "")).startswith("runtime.auth.exchange.")
    } == {"workload_identity_token"}
    assert "HTTP Request: POST" in captured.out  # the HTTP client's own log lines were captured
    # The decoding error and the bytes it held do not travel with the error.
    assert not any(isinstance(link, UnicodeDecodeError) for link in exception_chain(not_text.value))
    # Nothing besides the token file itself holds the token: not the workspace, not Tau's state.
    stored_files = [
        path
        for root in (tmp_path, settings.state_root)
        for path in root.rglob("*")
        if path.is_file() and path != token_file
    ]
    for token in map(projected_token, range(1, 5)):
        assert token not in captured.out
        assert token not in captured.err
        for error in errors:
            for link in exception_chain(error):
                assert token not in str(link)
                assert token not in repr(link)
                assert token not in repr(getattr(link, "detail", None))
        assert token not in json.dumps(headers)
        assert token not in repr(vars(auth))
        assert token not in repr(settings)
        assert token not in settings.model_dump_json()
        assert all(token not in value for value in os.environ.values())
        assert all(token.encode() not in path.read_bytes() for path in stored_files)


async def test_a_runtime_deployed_with_a_token_file_and_no_secret_starts_ready(
    tmp_path, monkeypatch, asgi_client
):
    token_file = project(tmp_path / "runtime-identity", projected_token(1))
    monkeypatch.setenv("MAINSEQUENCE_AUTH_MODE", "runtime_credential")
    monkeypatch.setenv("MAINSEQUENCE_RUNTIME_CREDENTIAL_ID", CREDENTIAL_ID)
    monkeypatch.setenv(TOKEN_FILE_ENV, str(token_file))
    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        backend_url=BACKEND,
        state_root=tmp_path / "tau-state",
        exclude_mainsequence_mcp=True,
    )
    exchange = StandInExchange(GRANTED)
    services, http = managed_services(settings, exchange)
    app = create_app(settings, services_factory=lambda _settings: services)

    async with asgi_client(app, lifespan=True) as client:
        health = (await client.get("/health")).json()
        ready = await client.get("/ready")
    await http.aclose()

    assert health["mode"] == "managed"
    assert health["mainsequence_auth_ready"] is True
    assert health["mainsequence_auth_source"] is None
    assert ready.status_code == 200
    assert exchange.bodies == [
        {"credential_id": CREDENTIAL_ID, "workload_identity_token": projected_token(1)}
    ]
    assert projected_token(1) not in json.dumps(health)


async def test_startup_with_a_missing_token_file_fails_before_any_request(tmp_path):
    missing = tmp_path / "runtime-identity" / "token"
    settings = managed_settings(
        tmp_path,
        runtime_identity_token_file=missing,
        runtime_credential_secret=SECRET,
        state_root=tmp_path / "tau-state",
        exclude_mainsequence_mcp=True,
    )
    exchange = StandInExchange(GRANTED)
    services, http = managed_services(settings, exchange)

    with pytest.raises(ConfigurationError, match=re.escape(f"{TOKEN_FILE_ENV} names {missing}, ")):
        await services.start()
    await http.aclose()

    assert exchange.bodies == []
