"""Async runtime-credential token exchange."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx
import structlog

from ms_tau_sdk.errors import BackendError, ConfigurationError, TauSDKError
from ms_tau_sdk.settings import (
    ACCESS_TOKEN_ENV,
    ENV_FILE,
    REFRESH_TOKEN_ENV,
    RUNTIME_CREDENTIAL_SECRET_ENV,
    RUNTIME_IDENTITY_TOKEN_FILE_ENV,
    TauSDKSettings,
)

from .routes import RUNTIME_CREDENTIAL_TOKEN

logger = structlog.get_logger(__name__)
JWT_REFRESH_PATH = "/auth/jwt-token/token/refresh/"
CLI_TOKEN_COMMAND = ("auth", "token", "--json")
CLI_TOKEN_TIMEOUT_SECONDS = 15.0
CLI_TOKEN_REUSE_MARGIN_SECONDS = 60
# The runtime credential exchange retries the answers that mean "not now": 429 (throttled) and
# 503 (verification temporarily unavailable). A 401 is final.
EXCHANGE_RETRY_STATUS_CODES = frozenset({429, 503})
EXCHANGE_MAX_RETRIES = 3
EXCHANGE_RETRY_BASE_DELAY_SECONDS = 1.0
EXCHANGE_MAX_RETRY_DELAY_SECONDS = 60.0
# A projected token is a JWT of a few kilobytes. A larger file is not a token file.
IDENTITY_TOKEN_MAX_BYTES = 64 * 1024

type RuntimeCredentialProof = Literal["workload_identity_token", "credential_secret"]


class BackendAuth(Protocol):
    """Authentication boundary shared by backend HTTP and MCP transports."""

    def bind_client(self, client: httpx.AsyncClient) -> None: ...

    async def headers(self, *, force: bool = False) -> dict[str, str]: ...

    async def prefetch(self) -> None: ...


@dataclass(slots=True)
class AccessToken:
    value: str = field(repr=False)  # the bearer token itself never appears in repr() or str()
    token_type: str
    expires_at: float | None

    def needs_refresh(self, *, skew_seconds: int = 30) -> bool:
        return self.expires_at is not None and self.expires_at <= time.time() + skew_seconds


def _read_workload_identity_token(path: Path) -> str:
    """Read the projected workload identity token for one exchange.

    The token file is rotated, so it is read for every exchange and the token is never cached.
    The token leaves this function only as its return value. An error names the file and the
    reason, never any of its content, and no error falls back to the runtime credential secret.
    """

    def unusable(reason: str) -> ConfigurationError:
        return ConfigurationError(
            f"{RUNTIME_IDENTITY_TOKEN_FILE_ENV} names {path}, which {reason}. The runtime "
            f"credential exchange does not fall back to {RUNTIME_CREDENTIAL_SECRET_ENV}."
        )

    try:
        with path.open("rb") as handle:
            content = handle.read(IDENTITY_TOKEN_MAX_BYTES + 1)
    except OSError as error:
        raise unusable(f"cannot be read ({error.strerror or type(error).__name__})") from None
    if len(content) > IDENTITY_TOKEN_MAX_BYTES:
        raise unusable(
            f"is larger than {IDENTITY_TOKEN_MAX_BYTES // 1024} KiB, too large for a token"
        )
    try:
        token: str | None = content.decode("utf-8").strip()
    except UnicodeDecodeError:
        # Raised outside this handler, so the decoding error and the bytes it holds stay behind.
        token = None
    if token is None:
        raise unusable("does not hold text")
    if not token:
        raise unusable("is empty")
    return token


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Return the wait a ``Retry-After`` header asks for, or None when there is no usable one."""

    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _exchange_retry_delay(response: httpx.Response, *, retry: int) -> float:
    """Wait as long as the platform asks, or back off exponentially when it does not say."""

    asked = _retry_after_seconds(response)
    if asked is not None:
        return asked
    return EXCHANGE_RETRY_BASE_DELAY_SECONDS * 2.0 ** (retry - 1)


def _rejection_message(proof: RuntimeCredentialProof) -> str:
    """Explain a 401 from the exchange by the names of the settings involved, never a value."""

    if proof == "workload_identity_token":
        return (
            "Runtime credential exchange was rejected (HTTP 401): the platform did not accept "
            f"the workload identity token read from {RUNTIME_IDENTITY_TOKEN_FILE_ENV}. A rejected "
            "token is not retried, and the exchange does not fall back to "
            f"{RUNTIME_CREDENTIAL_SECRET_ENV}."
        )
    return (
        "Runtime credential exchange was rejected (HTTP 401): the platform did not accept "
        f"{RUNTIME_CREDENTIAL_SECRET_ENV}. A rejected credential is not retried."
    )


class RuntimeCredentialAuth:
    """Exchange the managed runtime credential for short-lived Main Sequence access tokens.

    The exchange proves the credential in one of two ways. With
    ``MAINSEQUENCE_RUNTIME_IDENTITY_TOKEN_FILE`` set, it sends the projected workload identity
    token read from that file for every exchange, and never the secret. Without it, it sends
    ``MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET``. The token stays inside the exchange: it is not
    kept on this object, in the settings, or in the environment, and it is never logged or put
    in an error.
    """

    def __init__(
        self,
        settings: TauSDKSettings,
        *,
        exchange_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = exchange_client
        self._token: AccessToken | None = None
        self._lock = asyncio.Lock()

    def bind_client(self, client: httpx.AsyncClient) -> None:
        """Use the lifecycle-managed backend pool for token exchange."""
        if self._client is None:
            self._client = client

    async def headers(self, *, force: bool = False) -> dict[str, str]:
        token = await self._access_token(force=force)
        return {"Authorization": f"{token.token_type} {token.value}"}

    async def prefetch(self) -> None:
        """Authenticate during process startup so readiness is truthful."""
        await self._access_token(force=False)

    def _proof(self) -> RuntimeCredentialProof:
        """Name the proof the exchange sends. It is a field name, never a value."""
        if self.settings.runtime_identity_token_file is not None:
            return "workload_identity_token"
        return "credential_secret"

    async def _access_token(self, *, force: bool) -> AccessToken:
        if not force and self._token is not None and not self._token.needs_refresh():
            return self._token
        async with self._lock:
            if not force and self._token is not None and not self._token.needs_refresh():
                return self._token
            self.settings.validate_runtime_auth()
            proof = self._proof()
            client = self._client or httpx.AsyncClient(timeout=10)
            close_client = self._client is None
            started_at = time.monotonic()
            try:
                response, attempts = await self._exchange(client, proof)
            except Exception as failure:
                logger.warning(
                    "runtime.auth.exchange.completed",
                    duration_ms=round((time.monotonic() - started_at) * 1000, 3),
                    outcome="failed",
                    proof=proof,
                    error_type=type(failure).__name__,
                    status_code=getattr(failure, "backend_status", None),
                )
                raise
            finally:
                if close_client:
                    await client.aclose()
            data = response.json()
            value = str(data.get("access") or "").strip()
            if not value:
                raise BackendError("Runtime credential exchange returned no access token")
            expires_in = data.get("expires_in")
            expires_at = (
                time.time() + int(expires_in)
                if isinstance(expires_in, (int, float)) and expires_in > 0
                else None
            )
            self._token = AccessToken(
                value=value,
                token_type=str(data.get("token_type") or "Bearer"),
                expires_at=expires_at,
            )
            logger.info(
                "runtime.auth.exchange.completed",
                duration_ms=round((time.monotonic() - started_at) * 1000, 3),
                outcome="success",
                proof=proof,
                attempts=attempts,
            )
            return self._token

    async def _exchange(
        self,
        client: httpx.AsyncClient,
        proof: RuntimeCredentialProof,
    ) -> tuple[httpx.Response, int]:
        """Send the exchange, and send it again a bounded number of times after 429 or 503."""
        url = f"{self.settings.backend_url.rstrip('/')}{RUNTIME_CREDENTIAL_TOKEN}"
        attempt = 1
        while True:
            # Each attempt builds its request again, so a rotated token file is read again.
            response = await client.post(url, json=await self._exchange_body())
            status = response.status_code
            if response.is_success:
                return response, attempt
            if status == 401:
                raise BackendError(_rejection_message(proof), status_code=status)
            if status not in EXCHANGE_RETRY_STATUS_CODES:
                raise BackendError(
                    f"Runtime credential exchange failed (HTTP {status})", status_code=status
                )
            meaning = (
                "the platform throttled the exchange"
                if status == 429
                else "the platform could not verify the runtime credential"
            )
            if attempt > EXCHANGE_MAX_RETRIES:
                raise BackendError(
                    f"Runtime credential exchange failed (HTTP {status}) after {attempt} "
                    f"attempts: {meaning}. Try again later.",
                    status_code=status,
                )
            delay = _exchange_retry_delay(response, retry=attempt)
            if delay > EXCHANGE_MAX_RETRY_DELAY_SECONDS:
                raise BackendError(
                    f"Runtime credential exchange failed (HTTP {status}): {meaning} and asked "
                    f"to retry after {delay:.0f} seconds, longer than the "
                    f"{EXCHANGE_MAX_RETRY_DELAY_SECONDS:.0f} seconds the SDK waits.",
                    status_code=status,
                )
            logger.warning(
                "runtime.auth.exchange.retrying",
                status_code=status,
                attempt=attempt,
                max_attempts=EXCHANGE_MAX_RETRIES + 1,
                delay_seconds=delay,
                proof=proof,
            )
            await asyncio.sleep(delay)
            attempt += 1

    async def _exchange_body(self) -> dict[str, str]:
        """Build one exchange request. It carries exactly one proof of the credential."""
        credential_id = self.settings.runtime_credential_id
        if not credential_id:
            raise ConfigurationError("Runtime credentials are not configured")
        token_file = self.settings.runtime_identity_token_file
        if token_file is not None:
            token = await asyncio.to_thread(_read_workload_identity_token, token_file)
            return {"credential_id": credential_id, "workload_identity_token": token}
        secret = self.settings.runtime_credential_secret
        if not secret:
            raise ConfigurationError("Runtime credentials are not configured")
        return {"credential_id": credential_id, "credential_secret": secret}


def _jwt_expiry(token: str) -> int | None:
    """Read an unverified expiry only to decide when authenticated refresh is due."""

    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        expiry = decoded.get("exp")
        return int(expiry) if expiry is not None else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


class JWTAuth:
    """Dependency-free Main Sequence user JWT authentication for local mode."""

    def __init__(
        self,
        settings: TauSDKSettings,
        *,
        exchange_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = exchange_client
        self._access_token = (
            settings.access_token.get_secret_value().strip() if settings.access_token else ""
        )
        self._refresh_token = (
            settings.refresh_token.get_secret_value().strip() if settings.refresh_token else ""
        )
        self._lock = asyncio.Lock()

    def bind_client(self, client: httpx.AsyncClient) -> None:
        if self._client is None:
            self._client = client

    def _needs_refresh(self, *, skew_seconds: int = 60) -> bool:
        if not self._access_token:
            return True
        expiry = _jwt_expiry(self._access_token)
        return expiry is not None and expiry <= int(time.time()) + skew_seconds

    async def headers(self, *, force: bool = False) -> dict[str, str]:
        if force or self._needs_refresh():
            await self._refresh(force=force)
        if not self._access_token:
            raise ConfigurationError("MAINSEQUENCE_ACCESS_TOKEN is not configured")
        return {"Authorization": f"Bearer {self._access_token}"}

    async def prefetch(self) -> None:
        self.settings.validate_runtime_auth()
        await self.headers()

    async def _refresh(self, *, force: bool) -> None:
        async with self._lock:
            if not force and not self._needs_refresh():
                return
            if not self._refresh_token:
                raise ConfigurationError("MAINSEQUENCE_REFRESH_TOKEN is not configured")
            client = self._client or httpx.AsyncClient(timeout=10)
            close_client = self._client is None
            started_at = time.monotonic()
            try:
                response = await client.post(
                    f"{self.settings.backend_url.rstrip('/')}{JWT_REFRESH_PATH}",
                    json={"refresh": self._refresh_token},
                )
            finally:
                if close_client:
                    await client.aclose()
            if not response.is_success:
                raise BackendError(
                    "Main Sequence JWT refresh failed",
                    status_code=response.status_code,
                )
            data = response.json()
            access = str(data.get("access") or "").strip()
            if not access:
                raise BackendError("Main Sequence JWT refresh returned no access token")
            refresh = str(data.get("refresh") or "").strip()
            self._access_token = access
            if refresh:
                self._refresh_token = refresh
            logger.info(
                "local.auth.refresh.completed",
                duration_ms=round((time.monotonic() - started_at) * 1000, 3),
                outcome="success",
            )


@dataclass(frozen=True, slots=True)
class _CLITokenAnswer:
    """One token command run, reduced so that the command's stdout is never kept."""

    exit_code: int | None  # None when the command did not finish in time
    token: AccessToken | None = None
    endpoint: str = ""
    problem: str = ""  # why the output of a successful run is not the token contract
    stderr_line: str = ""


def _first_line(text: str, *, limit: int = 200) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return ""


def _display_url(url: str) -> str | None:
    """Return an HTTP URL without credentials or query, or None when it is not one."""

    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    location = f"{host}:{port}" if port else host
    return f"{parts.scheme}://{location}{parts.path}".rstrip("/")[:200]


def _read_cli_token(stdout: str, stderr_line: str) -> _CLITokenAnswer:
    """Check a successful run's stdout against the token command's JSON contract."""

    def rejected(problem: str) -> _CLITokenAnswer:
        return _CLITokenAnswer(exit_code=0, problem=problem, stderr_line=stderr_line)

    try:
        data = json.loads(stdout)
    except (ValueError, RecursionError):
        return rejected("it is not JSON")
    if not isinstance(data, dict):
        return rejected("it is not a JSON object")
    endpoint = data.get("endpoint")
    if not isinstance(endpoint, str) or _display_url(endpoint) is None:
        return rejected("`endpoint` is not an HTTP URL")
    value = data.get("access_token")
    if not isinstance(value, str) or not value or any(map(str.isspace, value)):
        return rejected("`access_token` is missing")
    token_type = data.get("token_type")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        return rejected("`token_type` is not Bearer")
    if "expires_at" not in data:
        return rejected("`expires_at` is missing")
    expires_at = data["expires_at"]
    if expires_at is not None and (
        isinstance(expires_at, bool)
        or not isinstance(expires_at, int | float)
        or not math.isfinite(expires_at)
    ):
        return rejected("`expires_at` is not a number or null")
    return _CLITokenAnswer(
        exit_code=0,
        token=AccessToken(
            value=value,
            token_type="Bearer",
            expires_at=None if expires_at is None else float(expires_at),
        ),
        endpoint=endpoint.strip(),
        stderr_line=stderr_line,
    )


def _run_cli_token_command(cli: Path, environment: dict[str, str]) -> _CLITokenAnswer:
    """Run the token command in a worker thread and reduce its output.

    The argument list is executed directly, never through a shell. Stdout holds the token, so
    it stays inside this function and only the parsed answer leaves it.
    """

    try:
        completed = subprocess.run(
            [str(cli), *CLI_TOKEN_COMMAND],
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLI_TOKEN_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # The exception carries the partial output, so it is not raised any further.
        return _CLITokenAnswer(exit_code=None)
    stderr_line = _first_line(completed.stderr)
    if completed.returncode != 0:
        return _CLITokenAnswer(exit_code=completed.returncode, stderr_line=stderr_line)
    return _read_cli_token(completed.stdout, stderr_line)


class CLITokenAuth:
    """Local-mode authentication that asks a Main Sequence CLI for the access token.

    The CLI owns the login session and its renewal. This provider runs the CLI's documented
    ``auth token --json`` command as a separate process and keeps only the returned access
    token, in memory. It does not import the ``mainsequence`` package and does not read the
    CLI's credential store.
    """

    def __init__(self, settings: TauSDKSettings) -> None:
        self.settings = settings
        self._token: AccessToken | None = None
        self._command_runs = 0
        self._lock = asyncio.Lock()

    def bind_client(self, client: httpx.AsyncClient) -> None:
        """The token comes from a local command, so there is no HTTP client to share."""
        del client

    async def headers(self, *, force: bool = False) -> dict[str, str]:
        token = await self._access_token(force=force)
        return {"Authorization": f"{token.token_type} {token.value}"}

    async def prefetch(self) -> None:
        self.settings.validate_runtime_auth()
        await self._access_token(force=False)

    def _reusable_token(self) -> AccessToken | None:
        token = self._token
        if token is None or token.needs_refresh(skew_seconds=CLI_TOKEN_REUSE_MARGIN_SECONDS):
            return None
        return token

    async def _access_token(self, *, force: bool) -> AccessToken:
        if not force and (token := self._reusable_token()) is not None:
            return token
        runs_seen = self._command_runs
        async with self._lock:
            # Callers that waited on the same command run share its token. A forced caller
            # accepts any token obtained after it asked.
            token = self._reusable_token()
            if token is not None and (not force or self._command_runs != runs_seen):
                return token
            token = await self._ask_cli()
            self._token = token
            self._command_runs += 1
            return token

    def _child_environment(self) -> dict[str, str]:
        """Make the CLI answer for Tau's backend from its saved session."""
        environment = dict(os.environ)
        # A token pair left in the environment must not take the place of the saved session.
        environment.pop(ACCESS_TOKEN_ENV, None)
        environment.pop(REFRESH_TOKEN_ENV, None)
        environment["MAINSEQUENCE_ENDPOINT"] = self.settings.backend_url
        return environment

    async def _ask_cli(self) -> AccessToken:
        cli = self.settings.mainsequence_cli_path()
        started_at = time.monotonic()
        result: AccessToken | tuple[str, TauSDKError]
        try:
            answer = await asyncio.to_thread(_run_cli_token_command, cli, self._child_environment())
        except OSError as spawn_error:
            reason = spawn_error.strerror or type(spawn_error).__name__
            result = (
                "not_started",
                ConfigurationError(
                    f"The Main Sequence CLI at {cli} could not be started ({reason}). "
                    "Check that the file is executable."
                ),
            )
        else:
            result = self._judge(cli, answer)
        duration_ms = round((time.monotonic() - started_at) * 1000, 3)
        if isinstance(result, AccessToken):
            logger.info(
                "local.auth.cli_token.completed", duration_ms=duration_ms, outcome="success"
            )
            return result
        outcome, failure = result
        logger.warning("local.auth.cli_token.completed", duration_ms=duration_ms, outcome=outcome)
        raise failure

    def _judge(self, cli: Path, answer: _CLITokenAnswer) -> AccessToken | tuple[str, TauSDKError]:
        """Return the token of one command run, or its outcome name and actionable error."""
        configured = _display_url(self.settings.backend_url) or "the configured backend"
        said = f" The CLI said: {answer.stderr_line}" if answer.stderr_line else ""
        pair = f"{ACCESS_TOKEN_ENV} and {REFRESH_TOKEN_ENV}"
        if answer.exit_code is None:
            return "timeout", BackendError(
                f"The Main Sequence CLI at {cli} did not answer `auth token` within "
                f"{CLI_TOKEN_TIMEOUT_SECONDS:g} seconds and was stopped."
            )
        if answer.exit_code == 1:
            return "no_session", ConfigurationError(
                f"The Main Sequence CLI at {cli} has no usable session for {configured}. "
                f"Run `mainsequence login`.{said}"
            )
        if answer.exit_code == 2:
            return "cli_too_old", ConfigurationError(
                f"The Main Sequence CLI at {cli} does not know `auth token` (exit code 2). "
                f"It is too old. Upgrade it, or provide {pair} in the environment."
            )
        if answer.exit_code == 3:
            return "no_credential_store", ConfigurationError(
                f"The Main Sequence CLI at {cli} found no credential store on this machine and "
                f"no credentials in the environment. Provide {pair} in the environment.{said}"
            )
        if answer.exit_code != 0:
            return "failed", BackendError(
                f"The Main Sequence CLI at {cli} failed `auth token` with exit code "
                f"{answer.exit_code}.{said}"
            )
        if answer.token is None:
            return "malformed_output", BackendError(
                f"The Main Sequence CLI at {cli} answered `auth token --json` with output that "
                f"is not the documented token contract: {answer.problem}. "
                "The CLI may be too old. Upgrade it."
            )
        if answer.endpoint.rstrip("/") != self.settings.backend_url.rstrip("/"):
            return "endpoint_mismatch", ConfigurationError(
                f"The Main Sequence CLI answered for {_display_url(answer.endpoint)}, but Tau "
                f"is configured for {configured}. Set MAINSEQUENCE_ENDPOINT to the backend of "
                "the CLI session, or run `mainsequence login` for the configured backend."
            )
        return answer.token


def local_mode_auth(settings: TauSDKSettings) -> BackendAuth:
    """Select the local-mode credential source. This is the only place that chooses it."""

    if settings.uses_cli_session():
        return CLITokenAuth(settings)
    variables = list(settings.env_file_token_names())
    if variables:
        env_file = Path(ENV_FILE).absolute()
        logger.warning(
            "local.auth.env_file_tokens.deprecated",
            message=(
                f"Local mode read {' and '.join(variables)} from {env_file}. Tokens in a "
                "project file are deprecated. Run `mainsequence refresh-token` in that "
                "directory to remove them. Local mode then uses the Main Sequence CLI session."
            ),
            env_file=str(env_file),
            variables=variables,
        )
    return JWTAuth(settings)
