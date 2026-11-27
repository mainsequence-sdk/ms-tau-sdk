"""Custom provider credential hydrate endpoints (ADR 0027).

tdag-django's ``POST /api/v1/model-provider-credentials/hydrate/`` defines the hydrate contract. A
custom endpoint implements the same contract for people the platform does not know:

- a FastAPI release that ``TAU_PROVIDER_CREDENTIAL_HYDRATE_RELEASE_UID`` names, in
  hosted and in local mode. The runtime calls it with a short-lived token the platform mints for
  that release (``resolve-runtime-access``, without a delegation); and
- in local mode only, the developer's own loopback service that
  ``TAU_LOCAL_PROVIDER_CREDENTIAL_HYDRATE_URL`` names. Nothing leaves the machine, so the call
  carries no authorization at all and the service decides the caller itself.

Only the token, to the release, crosses: never the runtime's Main Sequence credential, a caller
assertion, a lease token, caller context or an ``X-MainSequence-*`` header. No answer, token or
credential value is logged or kept in an error.

In a hosted session the release also serves the model relay (ADR 0030, section 7). Its model calls
are admitted as the hydrate call is: ``ReleaseRelayAuth`` sends the same token, only to an address
under the release's RPC URL, and moves the session pass into its own header.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import unquote

import httpx
import structlog

from ms_tau_sdk.errors import (
    BackendConflictError,
    BackendError,
    BackendTimeoutError,
    ConfigurationError,
)
from ms_tau_sdk.logging import TraceSpan
from ms_tau_sdk.model_relay import RELAY_PASS_HEADER, RELAY_PASS_PLACE_HEADER
from ms_tau_sdk.settings import TauSDKSettings

from .models import ReleaseRuntimeAccess
from .release_access import ReleaseAccess, release_access_from_answer
from .release_readiness import release_warmth, wait_until_ready
from .routes import model_provider_credentials

logger = structlog.get_logger(__name__)

HYDRATE_PATH = model_provider_credentials("hydrate")
RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})
_ATTEMPTS = 3
_ERROR_CODE_LIMIT = 128
_ERROR_DETAIL_LIMIT = 500

type HttpClientFactory = Callable[[], httpx.AsyncClient]
type ResolveReleaseAccess = Callable[[str], Awaitable[ReleaseRuntimeAccess]]


def _refusal_detail(response: httpx.Response) -> dict[str, str] | None:
    """The refusal's ``error_code`` and truncated ``error_detail``, and nothing else."""

    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, Mapping):
        return None
    detail: dict[str, str] = {}
    code = body.get("error_code")
    text = body.get("error_detail")
    if isinstance(code, str):
        detail["error_code"] = code[:_ERROR_CODE_LIMIT]
    if isinstance(text, str):
        detail["error_detail"] = text[:_ERROR_DETAIL_LIMIT]
    return detail or None


def on_release(url: httpx.URL | str, rpc_url: httpx.URL) -> bool:
    """Whether ``url`` is the release's RPC URL or below it, the only place its token may go."""

    url = httpx.URL(url)
    if (
        url.scheme,
        url.host,
        url.port,
        url.username,
        url.password,
    ) != (rpc_url.scheme, rpc_url.host, rpc_url.port, "", ""):
        return False
    # httpx resolves literal dot segments, not encoded ones, which a server may resolve later.
    if any(unquote(segment) in {".", ".."} for segment in url.raw_path.decode().split("/")):
        return False
    base = rpc_url.path.rstrip("/")
    return url.path == base or url.path.startswith(f"{base}/")


def _log_fields(detail: Mapping[str, str] | None) -> dict[str, str]:
    if not detail:
        return {}
    fields: dict[str, str] = {}
    if "error_code" in detail:
        fields["backend_error_code"] = detail["error_code"]
    if "error_detail" in detail:
        fields["backend_error_detail"] = detail["error_detail"]
    return fields


class CustomHydrateEndpoint:
    """One custom hydrate endpoint. It posts the hydrate body and returns the parsed answer.

    The answer is checked against the contract by ``MainSequenceClient``; here only the HTTP
    exchange is handled: the address, the authorization, retries and refusals.
    """

    target_system = "provider_credential_hydrate_endpoint"
    description = "The provider credential hydrate endpoint"

    def __init__(self, settings: TauSDKSettings, *, http: HttpClientFactory) -> None:
        self.settings = settings
        self._http = http

    @property
    def dependency_fields(self) -> dict[str, str]:
        """What the call's ``dependency.call.*`` lines name as its dependency (ADR 0031)."""

        return {"dependency_type": "api", "dependency_name": self.target_system}

    async def hydrate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Post ``payload`` and return the answer's JSON object, or raise without a value."""

        raise NotImplementedError

    async def _send(
        self,
        url: httpx.URL,
        payload: Mapping[str, Any],
        *,
        authorization: str | None,
    ) -> httpx.Response:
        """Post once, again on a transport failure or a server error, up to three times.

        The endpoint answers a repeated request the same way (ADR 0027, section 6).
        """

        headers = {"Accept": "application/json"}
        if authorization is not None:
            headers["Authorization"] = f"Bearer {authorization}"
        attempt = 0
        while True:
            attempt += 1
            client = self._http()
            # Built here, never from the client's defaults, so that no cookie and no header the
            # client kept travels with it.
            request = httpx.Request(
                "POST",
                url,
                json=dict(payload),
                headers=headers,
                extensions={"timeout": client.timeout.as_dict()},
            )
            started_at = time.monotonic()
            # Each attempt is its own span. Hydration is sent no caller context, so the span is
            # recorded only by its line and names nothing to the endpoint.
            span = TraceSpan()
            failure: httpx.HTTPError | None = None
            response: httpx.Response | None = None
            try:
                response = await client.send(request, follow_redirects=False)
            except httpx.HTTPError as error:
                failure = error
            finally:
                client.cookies.clear()
            duration_ms = round((time.monotonic() - started_at) * 1000, 3)
            if response is None:
                assert failure is not None
                retryable = isinstance(failure, httpx.TransportError) and attempt < _ATTEMPTS
                logger.warning(
                    "dependency.call.failed",
                    message="Provider credential hydration call failed",
                    **span.log_fields,
                    **self.dependency_fields,
                    dependency_operation=f"POST {HYDRATE_PATH}",
                    target_system=self.target_system,
                    dependency_attempt=attempt,
                    duration_ms=duration_ms,
                    error_type=type(failure).__name__,
                    outcome="failed",
                    retryable=retryable,
                )
                if retryable:
                    await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
                    continue
                # Raised outside the handler: the error holds the request and its token.
                if isinstance(failure, httpx.TimeoutException):
                    raise BackendTimeoutError(
                        f"{self.description} did not answer in time",
                        timeout_seconds=self.settings.backend_read_timeout_seconds,
                    )
                raise BackendError(
                    f"{self.description} could not be reached: {type(failure).__name__}"
                )
            retryable = response.status_code in RETRYABLE_STATUS_CODES and attempt < _ATTEMPTS
            logger.info(
                "dependency.call.completed",
                message="Provider credential hydration call completed",
                **span.log_fields,
                **self.dependency_fields,
                dependency_operation=f"POST {HYDRATE_PATH}",
                target_system=self.target_system,
                dependency_attempt=attempt,
                status_code=response.status_code,
                duration_ms=duration_ms,
                outcome=(
                    "success"
                    if response.is_success
                    else "rejected"
                    if response.status_code < 500
                    else "failed"
                ),
                retryable=retryable,
                **(_log_fields(_refusal_detail(response)) if not response.is_success else {}),
            )
            if retryable:
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
                continue
            return response

    def _answer(self, response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        if status == 409:
            raise BackendConflictError(
                f"{self.description} refused the hydration",
                status_code=409,
                detail=_refusal_detail(response),
            )
        if status == 403:
            raise BackendError(
                f"{self.description} refused this runtime",
                status_code=403,
                detail=_refusal_detail(response),
            )
        if not response.is_success:
            raise BackendError(
                f"{self.description} failed with HTTP {status}",
                status_code=status,
            )
        if len(response.content) > self.settings.backend_max_response_bytes:
            raise BackendError(f"{self.description} answer exceeded the configured size limit")
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise BackendError(f"{self.description} answered a body that is not a JSON object")
        return data


class LocalAddressHydrateEndpoint(CustomHydrateEndpoint):
    """The developer's own loopback service, in local mode. The call carries no authorization."""

    target_system = "local_provider_credential_hydrate_address"
    description = "The local provider credential hydrate address"

    def __init__(self, settings: TauSDKSettings, origin: str, *, http: HttpClientFactory) -> None:
        super().__init__(settings, http=http)
        self.url = httpx.URL(origin.rstrip("/") + HYDRATE_PATH)

    async def hydrate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        response = await self._send(self.url, payload, authorization=None)
        return self._answer(response)


class ReleaseHydrateEndpoint(CustomHydrateEndpoint):
    """A FastAPI release, called with a token the platform mints for it.

    The token is the runtime's own (no delegation): hydration serves the session, not the person
    of a turn. It is kept in memory until it nears expiry, and a ``401`` obtains a new one once.
    """

    target_system = "provider_credential_hydrate_release"
    description = "The provider credential hydrate release"

    def __init__(
        self,
        settings: TauSDKSettings,
        release_uid: str,
        *,
        resolve: ResolveReleaseAccess,
        http: HttpClientFactory,
    ) -> None:
        super().__init__(settings, http=http)
        self.release_uid = release_uid
        self._resolve = resolve
        self._access: ReleaseAccess | None = None
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"ReleaseHydrateEndpoint(release_uid={self.release_uid!r})"

    @property
    def dependency_fields(self) -> dict[str, str]:
        return {"dependency_type": "release", "dependency_name": self.release_uid}

    async def hydrate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            access = await self._release_access(stale=None)
            response = await self._send(
                self._url(access), payload, authorization=access.token.value
            )
            if response.status_code == 401:
                # The token may have expired early or been revoked: obtain a new one, once.
                access = await self._release_access(stale=access)
                response = await self._send(
                    self._url(access), payload, authorization=access.token.value
                )
                if response.status_code == 401:
                    raise BackendError(f"{self.description} refused its token", status_code=401)
        except BackendTimeoutError:
            # The request may have been held by a start: ask the platform before the next.
            release_warmth.forget(self.release_uid)
            raise
        if response.status_code < 500:
            release_warmth.served(self.release_uid)
        data = self._answer(response)
        self._refuse_relays_elsewhere(data, access)
        return data

    async def relay_access(self, *, stale: ReleaseAccess | None = None) -> ReleaseAccess:
        """The token and RPC URL a model call to this release's relay uses, as hydration does."""

        return await self._release_access(stale=stale)

    @staticmethod
    def _refuse_relays_elsewhere(data: Mapping[str, Any], access: ReleaseAccess) -> None:
        """Refuse a relay that is not the release's own: its token would follow the model calls."""

        credentials = data.get("credentials")
        if not isinstance(credentials, Mapping):
            return
        for entry in credentials.values():
            relay = entry.get("relay_definition") if isinstance(entry, Mapping) else None
            base_url = relay.get("base_url") if isinstance(relay, Mapping) else None
            if isinstance(base_url, str) and not on_release(base_url, access.rpc_url):
                raise ConfigurationError(
                    "The provider credential hydrate release answered a relay_definition "
                    "that is not under its own address"
                )

    def _url(self, access: ReleaseAccess) -> httpx.URL:
        url = httpx.URL(str(access.rpc_url).rstrip("/") + HYDRATE_PATH)
        if (url.scheme, url.host, url.port) != (
            access.rpc_url.scheme,
            access.rpc_url.host,
            access.rpc_url.port,
        ):
            raise BackendError("Backend release access names an invalid RPC URL")
        return url

    async def _release_access(self, *, stale: ReleaseAccess | None) -> ReleaseAccess:
        async with self._lock:
            cached = self._access
            if (
                cached is not None
                and cached is not stale
                and not cached.token.needs_refresh()
                # A release that may have scaled to zero is asked about again, which wakes it.
                and release_warmth.is_warm(self.release_uid)
            ):
                return cached
            self._access = None
            access = await self._ready_access()
            self._access = access
            return access

    async def _ready_access(self) -> ReleaseAccess:
        """Obtain token access, waiting while the release wakes, within the ready timeout."""

        async def obtain() -> ReleaseAccess:
            return release_access_from_answer(await self._runtime_access())

        return await wait_until_ready(
            obtain,
            settings=self.settings,
            release_uid=self.release_uid,
            event="runtime.provider_credential_release",
        )

    async def _runtime_access(self) -> ReleaseRuntimeAccess:
        failure: BackendError
        try:
            return await self._resolve(self.release_uid)
        except BackendError as error:
            # A transport failure keeps the request, and its credentials, as its cause, so only
            # safe metadata is carried on, including a timeout's classification.
            failure = (
                BackendTimeoutError(str(error), timeout_seconds=error.timeout_seconds)
                if isinstance(error, BackendTimeoutError)
                else BackendError(str(error), status_code=error.backend_status)
            )
            failure.detail = error.detail
        raise failure


class ReleaseRelayAuth(httpx.Auth):
    """Admit a model call at the relay the hydrate release serves, as its hydrate call is admitted.

    Request identity admits a call by its ``Authorization``, so the transport's pass moves to
    ``X-Model-Relay-Pass``, with its place, and ``Authorization`` carries the release token. The
    token goes only to an address under the release's RPC URL; a call anywhere else is refused
    before it is sent. A ``401`` gets a new token once.
    """

    requires_request_body = True

    def __init__(self, release: ReleaseHydrateEndpoint) -> None:
        self._release = release

    def __repr__(self) -> str:
        return f"ReleaseRelayAuth(release_uid={self._release.release_uid!r})"

    async def async_auth_flow(
        self,
        request: httpx.Request,
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        self._move_pass(request)
        access = await self._release.relay_access()
        self._authorize(request, access)
        response = yield request
        if response.status_code == 401:
            access = await self._release.relay_access(stale=access)
            self._authorize(request, access)
            response = yield request
        if response.status_code < 500:
            release_warmth.served(self._release.release_uid)

    @staticmethod
    def _move_pass(request: httpx.Request) -> None:
        """Move the pass from where the transport put a secret, in the relay's order."""

        authorization = request.headers.get("authorization", "")
        if authorization[:7].lower() == "bearer " and authorization[7:].strip():
            place, session_pass = "bearer", authorization[7:].strip()
            del request.headers["authorization"]
        elif request.headers.get("x-api-key"):
            place, session_pass = "x-api-key", request.headers["x-api-key"]
            del request.headers["x-api-key"]
        elif request.url.params.get("key"):
            place, session_pass = "query", request.url.params["key"]
            request.url = request.url.copy_remove_param("key")
        else:
            raise ConfigurationError("A model call to the hydrate release carries no session pass")
        request.headers[RELAY_PASS_HEADER] = session_pass
        request.headers[RELAY_PASS_PLACE_HEADER] = place

    @staticmethod
    def _authorize(request: httpx.Request, access: ReleaseAccess) -> None:
        if not on_release(request.url, access.rpc_url):
            raise ConfigurationError(
                "The model relay is not under the provider credential hydrate release's address"
            )
        request.headers["Authorization"] = f"Bearer {access.token.value}"


__all__ = [
    "HYDRATE_PATH",
    "CustomHydrateEndpoint",
    "LocalAddressHydrateEndpoint",
    "ReleaseHydrateEndpoint",
    "ReleaseRelayAuth",
    "on_release",
]
