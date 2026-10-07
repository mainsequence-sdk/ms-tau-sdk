"""The person a turn serves, and the calls an extension tool makes for that person.

An Agent that an Organization admin enabled for it can read platform data, and call other platform
applications, with the access of the person whose request a turn is serving: the requester. The
platform keeps that authority. The runtime only proves which of its own sessions it is working on,
and the platform finds the person in its own records:

- a chat or A2A Message turn of a hosted runtime presents the verified caller assertion of the
  request that started it when it marks the turn active, and the platform answers with the person
  it recorded as the turn's requester, or nobody;
- a hosted request that creates or continues an A2A Task presents the assertion with that call, and
  a Task attempt takes the requester the platform recorded for the Task: from its answer to that
  request, or from its dispatch.

``current_requester()`` returns that person inside the turn. ``requester_client()`` makes
requester-bound calls for extension tools: each one names the session and carries the runtime's
lease proof beside its own credential. Neither exposes the assertion, the lease proof, the runtime
credential or an application token to the tool, and the binding ends with the turn.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import structlog

from ms_tau_sdk.backend.auth import AccessToken, _jwt_expiry
from ms_tau_sdk.backend.client import LeaseProof, answer_without_request_credentials
from ms_tau_sdk.backend.models import ReleaseRuntimeAccess
from ms_tau_sdk.errors import BackendError, TauSDKError

logger = structlog.get_logger(__name__)

REQUESTER_BINDING_INVALID = "requester_binding_invalid"
_REFUSED_MESSAGE = (
    "Requester-bound access was refused ({code}): the turn is over, the requester's access was "
    "removed, the request is more than 24 hours old, or this Agent is not enabled to act for its "
    "requester."
)


@dataclass(frozen=True, slots=True)
class Requester:
    """The verified person whose request the current turn serves.

    ``team_uids`` can be empty when the turn does not know them.
    """

    uid: str
    team_uids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CallerDelivery:
    """The caller delivery that a turn the platform starts resumes its session for.

    ``requester`` is the person the platform's verified signal names as the requester of the
    delegated work, or None. The turn serves that person only when the platform records the same
    person for the turn it starts.
    """

    uid: str
    requester: Requester | None = None


class RequesterBindingError(TauSDKError, PermissionError):
    """The turn has no verified requester, or the platform ended the requester's binding.

    ``code`` is ``requester_binding_invalid`` or the platform's ``runtime_lease_*`` code.
    """

    code = REQUESTER_BINDING_INVALID
    status_code = 403

    def __init__(self, message: str, *, code: str = REQUESTER_BINDING_INVALID) -> None:
        super().__init__(message)
        self.code = code


class _RequesterPlatform(Protocol):
    """The platform calls a turn binding makes, with the runtime's own credential."""

    async def requester_bound_request(
        self,
        method: str,
        path: str,
        *,
        proof: LeaseProof,
        params: Any = None,
        json: Any = None,
        content: bytes | str | None = None,
        data: Any = None,
        files: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response: ...

    async def resolve_release_runtime_access(
        self,
        release_uid: str,
        *,
        proof: LeaseProof,
    ) -> ReleaseRuntimeAccess: ...


@dataclass(frozen=True, slots=True)
class _TurnApplicationAccess:
    """SDK-private: one application's RPC URL and a token for the turn's requester.

    Never give the token to the model or to project code. ``renew`` obtains a new token for the
    same turn, from any task.
    """

    rpc_url: httpx.URL
    token: str = field(repr=False)
    renew: Callable[[], Awaitable[str]] = field(repr=False)


@dataclass(slots=True)
class _ReleaseAccess:
    rpc_url: httpx.URL
    token: AccessToken


class TurnRequesterBinding:
    """One turn's requester and the means to act for it. Private to the SDK.

    The turn that creates the binding closes it when it ends. A closed binding has no requester,
    and every call through it is refused before it is sent.
    """

    __slots__ = (
        "_active",
        "_application_http",
        "_lease_proof",
        "_lock",
        "_platform",
        "_release_access",
        "requester",
        "session_uid",
    )

    def __init__(
        self,
        *,
        session_uid: str,
        requester: Requester | None,
        lease_proof: Callable[[], LeaseProof],
        platform: _RequesterPlatform,
        application_http: Callable[[], httpx.AsyncClient],
    ) -> None:
        self.session_uid = session_uid
        self.requester = requester
        self._lease_proof = lease_proof
        self._platform = platform
        self._application_http = application_http
        self._release_access: dict[str, _ReleaseAccess] = {}
        self._lock = asyncio.Lock()
        self._active = True

    def __repr__(self) -> str:
        return f"TurnRequesterBinding(session_uid={self.session_uid!r}, active={self._active})"

    @property
    def active(self) -> bool:
        return self._active

    def close(self) -> None:
        """End the binding: no requester, no call, and no cached application token."""

        self._active = False
        self._release_access.clear()

    def _require_requester(self) -> None:
        if not self._active or self.requester is None:
            raise RequesterBindingError(
                "This turn has no verified requester, so it cannot act for one."
            )

    async def platform_request(
        self,
        method: str,
        path: str,
        options: dict[str, Any],
    ) -> httpx.Response:
        self._require_requester()
        response = await self._platform.requester_bound_request(
            method,
            path,
            proof=self._lease_proof(),
            **options,
        )
        _refuse_ended_binding(response)
        return response

    async def release_request(
        self,
        release_uid: str,
        method: str,
        path: str,
        options: dict[str, Any],
    ) -> httpx.Response:
        self._require_requester()
        refresh = False
        while True:
            access = await self._release_access_for(release_uid, refresh=refresh)
            url = httpx.URL(str(access.rpc_url).rstrip("/") + path)
            if (url.scheme, url.host, url.port) != (
                access.rpc_url.scheme,
                access.rpc_url.host,
                access.rpc_url.port,
            ):
                raise ValueError("A release call takes a path on the release's RPC URL")
            response = await self._send_to_application(
                release_uid,
                method,
                url,
                token=access.token.value,
                options=options,
            )
            _refuse_ended_binding(response)
            if response.status_code == 401 and not refresh:
                # The token may have expired early or been revoked: obtain a new one, once.
                refresh = True
                continue
            return response

    async def _release_access_for(self, release_uid: str, *, refresh: bool) -> _ReleaseAccess:
        async with self._lock:
            self._require_requester()
            cached = self._release_access.get(release_uid)
            if cached is not None and not refresh and not cached.token.needs_refresh():
                return cached
            self._release_access.pop(release_uid, None)
            failure: BackendError | None = None
            try:
                answer = await self._platform.resolve_release_runtime_access(
                    release_uid,
                    proof=self._lease_proof(),
                )
            except BackendError as error:
                # A transport failure keeps the request, and its credentials, as its cause, so
                # only the message, status and answer are carried on.
                failure = BackendError(str(error), status_code=error.backend_status)
                failure.detail = error.detail
            if failure is not None:
                if failure.backend_status == 403:
                    _refuse_ended_binding_detail(failure.detail)
                raise failure
            access = _token_access(answer)
            if self._active:
                self._release_access[release_uid] = access
            return access

    async def _send_to_application(
        self,
        release_uid: str,
        method: str,
        url: httpx.URL,
        *,
        token: str,
        options: dict[str, Any],
    ) -> httpx.Response:
        self._require_requester()
        client = self._application_http()
        # Only the application's bearer token goes to the application: never the runtime
        # credential or the lease proof. The request is built here so that no cookie the shared
        # client kept from another answer is sent with it.
        request = httpx.Request(
            method,
            url,
            params=options.get("params"),
            json=options.get("json"),
            content=options.get("content"),
            data=options.get("data"),
            files=options.get("files"),
            headers={**dict(options.get("headers") or {}), "Authorization": f"Bearer {token}"},
            extensions={"timeout": client.timeout.as_dict()},
        )
        started_at = time.monotonic()
        failure = ""
        try:
            response = await client.send(request, follow_redirects=False)
        except httpx.HTTPError as error:
            failure = type(error).__name__
        finally:
            client.cookies.clear()
        if failure:
            logger.warning(
                "dependency.call.failed",
                message="Requester-bound application call failed",
                target_system="resource_release",
                resource_release_uid=release_uid,
                requester_bound=True,
                duration_ms=round((time.monotonic() - started_at) * 1000, 3),
                error_type=failure,
                outcome="failed",
            )
            # Raised outside the handler: the error holds the request and its bearer token.
            raise BackendError(f"Requester-bound call to release {release_uid} failed: {failure}")
        logger.info(
            "dependency.call.completed",
            message="Requester-bound application call completed",
            target_system="resource_release",
            resource_release_uid=release_uid,
            requester_bound=True,
            status_code=response.status_code,
            duration_ms=round((time.monotonic() - started_at) * 1000, 3),
            outcome=(
                "success"
                if response.is_success
                else "rejected"
                if response.status_code < 500
                else "failed"
            ),
        )
        return answer_without_request_credentials(response)


class RequesterClient:
    """Requester-bound calls for an extension tool, bound to the turn that created it.

    Every call is read with the access of the turn's requester, and only while the turn runs.
    A refused binding raises an error that is a ``PermissionError``.
    """

    __slots__ = ("_binding",)

    def __init__(self, binding: TurnRequesterBinding) -> None:
        self._binding = binding

    def __repr__(self) -> str:
        return "RequesterClient()"

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json: Any = None,
        content: bytes | str | None = None,
        data: Any = None,
        files: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Call a platform API path, relative to the platform's base URL, for the requester."""

        return await self._binding.platform_request(
            _method(method),
            _relative_path(path),
            _options(
                params=params,
                json=json,
                content=content,
                data=data,
                files=files,
                headers=headers,
            ),
        )

    async def call_release(
        self,
        release_uid: str,
        method: str,
        path: str,
        *,
        params: Any = None,
        json: Any = None,
        content: bytes | str | None = None,
        data: Any = None,
        files: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Call another platform application, which answers as the requester.

        ``path`` is relative to the application's RPC URL. The access the platform grants is kept
        for the turn while it is valid.
        """

        return await self._binding.release_request(
            _canonical_uid(release_uid),
            _method(method),
            _relative_path(path),
            _options(
                params=params,
                json=json,
                content=content,
                data=data,
                files=files,
                headers=headers,
            ),
        )


_TURN_BINDING: ContextVar[TurnRequesterBinding | None] = ContextVar(
    "ms_tau_sdk_turn_requester_binding",
    default=None,
)


def current_requester() -> Requester | None:
    """Return the verified person the current turn serves, or None.

    - A chat or A2A Message turn of a hosted runtime: the verified caller of the request that
      started it, when the platform recorded that person as the turn's requester.
    - An A2A Task attempt: the person the platform recorded as the Task's requester, named in its
      answer to the request that created or continued the Task, or in its dispatch.
    - Otherwise None: an Agent caller, the platform's own calls, local mode, or outside a turn.
    """

    binding = _TURN_BINDING.get()
    if binding is None or not binding.active:
        return None
    return binding.requester


def requester_client() -> RequesterClient:
    """Return a client that makes requester-bound calls for the current turn.

    Raises an error that is a ``PermissionError`` when ``current_requester()`` is None.
    """

    binding = _TURN_BINDING.get()
    if binding is None or not binding.active or binding.requester is None:
        raise RequesterBindingError(
            "This turn has no verified requester, so it cannot act for one."
        )
    return RequesterClient(binding)


def bind_turn_requester(binding: TurnRequesterBinding) -> Token[TurnRequesterBinding | None]:
    """Make ``binding`` the current turn's requester binding."""

    return _TURN_BINDING.set(binding)


def unbind_turn_requester(
    binding: TurnRequesterBinding,
    token: Token[TurnRequesterBinding | None],
) -> None:
    """End ``binding`` when its turn ends, and restore the previous binding."""

    binding.close()
    # A turn finalized from another context cannot reset its own; it is closed regardless.
    with suppress(ValueError):
        _TURN_BINDING.reset(token)


def detach_turn_requester() -> None:
    """Leave the current turn's binding out of a detached task's context."""

    _TURN_BINDING.set(None)


def canonical_requester_uid(value: object) -> str | None:
    """Return ``value`` when it is a canonical lowercase UUID, and None otherwise."""

    if not isinstance(value, str):
        return None
    try:
        return value if str(uuid.UUID(value)) == value else None
    except ValueError:
        return None


async def _turn_application_access(release_uid: str) -> _TurnApplicationAccess:
    """SDK-private: the current turn's access to one application, for its requester."""

    binding = _TURN_BINDING.get()
    if binding is None:
        raise RequesterBindingError(
            "This turn has no verified requester, so it cannot act for one."
        )
    uid = _canonical_uid(release_uid)
    access = await binding._release_access_for(uid, refresh=False)

    async def renew() -> str:
        return (await binding._release_access_for(uid, refresh=True)).token.value

    return _TurnApplicationAccess(rpc_url=access.rpc_url, token=access.token.value, renew=renew)


def _canonical_uid(value: str) -> str:
    uid = canonical_requester_uid(value)
    if uid is None:
        raise ValueError("release_uid must be a canonical lowercase UUID")
    return uid


def _method(value: str) -> str:
    method = str(value).upper()
    if not method.isascii() or not method.isalpha():
        raise ValueError(f"Invalid HTTP method: {value!r}")
    return method


def _relative_path(path: str) -> str:
    """Accept only a path, so that a call can never be sent to another origin."""

    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or any(character.isspace() for character in path)
    ):
        raise ValueError("A requester-bound call takes a path such as /api/v1/..., never a URL")
    return path


def _options(*, headers: Mapping[str, str] | None, **values: Any) -> dict[str, Any]:
    if headers is not None:
        for name in headers:
            normalized = str(name).lower()
            if normalized in {"authorization", "proxy-authorization"} or normalized.startswith(
                "x-mainsequence-"
            ):
                raise ValueError(f"A requester-bound call sets {name} itself")
    options = {name: value for name, value in values.items() if value is not None}
    if headers is not None:
        options["headers"] = dict(headers)
    return options


def _refused_code(detail: object) -> str | None:
    code = detail.get("code") if isinstance(detail, Mapping) else None
    if isinstance(code, str) and (
        code == REQUESTER_BINDING_INVALID or code.startswith("runtime_lease_")
    ):
        return code
    return None


def _refuse_ended_binding_detail(detail: object) -> None:
    code = _refused_code(detail)
    if code is not None:
        raise RequesterBindingError(_REFUSED_MESSAGE.format(code=code), code=code)


def _refuse_ended_binding(response: httpx.Response) -> None:
    if response.status_code != 403:
        return
    try:
        detail = response.json()
    except ValueError:
        return
    _refuse_ended_binding_detail(detail)


def _token_access(answer: ReleaseRuntimeAccess) -> _ReleaseAccess:
    grant = answer.access
    if grant is None:
        state = answer.runtime_access.get("state")
        raise BackendError(
            f"Release {answer.resource_release_uid} is not ready to be called"
            + (f" (runtime access: {state})" if isinstance(state, str) else ""),
            detail={"runtime_access_state": state if isinstance(state, str) else None},
        )
    token = grant.token.get_secret_value() if grant.token is not None else ""
    if grant.mode != "token" or not token or not grant.rpc_url:
        raise BackendError(
            f"Release {answer.resource_release_uid} offers no token access to call it"
        )
    try:
        rpc_url: httpx.URL | None = httpx.URL(grant.rpc_url)
    except httpx.InvalidURL:
        rpc_url = None
    if (
        rpc_url is None
        or rpc_url.scheme not in {"https", "http"}
        or not rpc_url.host
        or rpc_url.userinfo
    ):
        raise BackendError("Backend release access names an invalid RPC URL")
    expires_at = (
        grant.expires_at.timestamp() if grant.expires_at is not None else _jwt_expiry(token)
    )
    return _ReleaseAccess(
        rpc_url=rpc_url,
        token=AccessToken(
            value=token,
            token_type="Bearer",
            expires_at=float(expires_at) if expires_at is not None else None,
        ),
    )


__all__ = [
    "CallerDelivery",
    "Requester",
    "RequesterBindingError",
    "RequesterClient",
    "TurnRequesterBinding",
    "bind_turn_requester",
    "current_requester",
    "detach_turn_requester",
    "requester_client",
    "unbind_turn_requester",
]
