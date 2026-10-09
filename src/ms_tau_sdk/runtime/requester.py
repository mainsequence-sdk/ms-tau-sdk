"""The person a turn serves, and the calls project code makes for the work.

An Agent that an Organization admin enabled for it can act with the access of the person whose
request a turn is serving: the requester. The platform keeps that authority. The runtime only
proves which of its own sessions it is working on, and the platform finds the person in its own
records. When a hosted turn or Task attempt starts, the platform's answer names that person, or
nobody; that answer is the only source.

``current_requester()`` returns that person inside the turn. ``platform_client()`` makes calls to
the platform and its applications for project code: by default each call carries the delegation
of the person the turn serves, when it serves one, and none otherwise, and the receiving operation
decides what the call may do. Neither exposes an assertion, the lease proof, the runtime
credential or an application token to the tool, and a turn's delegation ends with the turn.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx
import structlog

from ms_tau_sdk.backend.auth import AccessToken, _jwt_expiry
from ms_tau_sdk.backend.client import LeaseProof, answer_without_request_credentials
from ms_tau_sdk.backend.models import ReleaseRuntimeAccess
from ms_tau_sdk.errors import BackendError, BackendTimeoutError, TauSDKError

logger = structlog.get_logger(__name__)

REQUESTER_BINDING_INVALID = "requester_binding_invalid"
_REFUSED_MESSAGE = (
    "The delegation was refused ({code}): the turn is over, the person's access was removed, the "
    "request is more than 24 hours old, or this Agent is not enabled to act for its requester."
)

type Delegation = Literal["auto", "none", "required"]
_DELEGATIONS: tuple[Delegation, ...] = ("auto", "none", "required")


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

    The turn names the delivery when it starts, and the platform answers with the person the turn
    serves, or nobody.
    """

    uid: str


class RequesterBindingError(TauSDKError, PermissionError):
    """A delegated call cannot be made, or the platform ended the delegation.

    ``code`` is ``requester_binding_invalid`` or the platform's ``runtime_lease_*`` code.
    """

    code = REQUESTER_BINDING_INVALID
    status_code = 403

    def __init__(self, message: str, *, code: str = REQUESTER_BINDING_INVALID) -> None:
        super().__init__(message)
        self.code = code


class _RequesterPlatform(Protocol):
    """The platform calls a binding makes, with the runtime's own credential."""

    async def platform_request(
        self,
        method: str,
        path: str,
        *,
        proof: LeaseProof | None,
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
        proof: LeaseProof | None,
    ) -> ReleaseRuntimeAccess: ...


@dataclass(frozen=True, slots=True)
class _TurnApplicationAccess:
    """SDK-private: one application's RPC URL and a token for the turn's call.

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
    """One turn's person, if any, and the means to call for the work. Private to the SDK.

    A binding with a session belongs to one turn, which closes it when it ends. A closed binding
    serves nobody: a call through it carries no delegation. The process binding has no session and
    serves nobody; it carries the calls made outside a turn.
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
        session_uid: str | None,
        requester: Requester | None,
        lease_proof: Callable[[], LeaseProof] | None,
        platform: _RequesterPlatform,
        application_http: Callable[[], httpx.AsyncClient],
    ) -> None:
        if requester is not None and lease_proof is None:
            raise ValueError("A binding that serves a person needs its lease proof")
        self.session_uid = session_uid
        self.requester = requester
        self._lease_proof = lease_proof
        self._platform = platform
        self._application_http = application_http
        self._release_access: dict[tuple[str, bool], _ReleaseAccess] = {}
        self._lock = asyncio.Lock()
        self._active = True

    def __repr__(self) -> str:
        return f"TurnRequesterBinding(session_uid={self.session_uid!r}, active={self._active})"

    @property
    def active(self) -> bool:
        return self._active

    @property
    def serving(self) -> bool:
        """Whether a call made now carries the delegation of the person the turn serves."""

        return self._active and self.requester is not None

    def close(self) -> None:
        """End the turn's delegation and drop every cached application token."""

        self._active = False
        self._release_access.clear()

    def session_proof(self) -> LeaseProof:
        """The turn's session and the runtime's lease on it, as they are when a call is made."""

        if self._lease_proof is None:
            raise RuntimeError("This binding belongs to no session")
        return self._lease_proof()

    def _require_requester(self) -> None:
        if not self.serving:
            raise RequesterBindingError("This turn serves nobody, so it cannot act for a person.")

    def _delegation(self, delegate: bool) -> LeaseProof | None:
        if not delegate:
            return None
        self._require_requester()
        return self.session_proof()

    async def platform_request(
        self,
        method: str,
        path: str,
        options: dict[str, Any],
        *,
        delegate: bool,
    ) -> httpx.Response:
        response = await self._platform.platform_request(
            method,
            path,
            proof=self._delegation(delegate),
            **options,
        )
        if delegate:
            _refuse_ended_binding(response)
        return response

    async def release_request(
        self,
        release_uid: str,
        method: str,
        path: str,
        options: dict[str, Any],
        *,
        delegate: bool,
    ) -> httpx.Response:
        if delegate:
            self._require_requester()
        refresh = False
        while True:
            access = await self._release_access_for(release_uid, refresh=refresh, delegate=delegate)
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
                delegate=delegate,
            )
            if delegate:
                _refuse_ended_binding(response)
            if response.status_code == 401 and not refresh:
                # The token may have expired early or been revoked: obtain a new one, once.
                refresh = True
                continue
            return response

    async def _release_access_for(
        self,
        release_uid: str,
        *,
        refresh: bool,
        delegate: bool,
    ) -> _ReleaseAccess:
        async with self._lock:
            proof = self._delegation(delegate)
            key = (release_uid, delegate)
            cached = self._release_access.get(key)
            if cached is not None and not refresh and not cached.token.needs_refresh():
                return cached
            self._release_access.pop(key, None)
            failure: BackendError | None = None
            try:
                answer = await self._platform.resolve_release_runtime_access(
                    release_uid,
                    proof=proof,
                )
            except BackendError as error:
                # A transport failure keeps the request, and its credentials, as its cause, so
                # only safe metadata is carried on, including a timeout's classification.
                failure = (
                    BackendTimeoutError(str(error), timeout_seconds=error.timeout_seconds)
                    if isinstance(error, BackendTimeoutError)
                    else BackendError(str(error), status_code=error.backend_status)
                )
                failure.detail = error.detail
            if failure is not None:
                if delegate and failure.backend_status == 403:
                    _refuse_ended_binding_detail(failure.detail)
                raise failure
            access = _token_access(answer)
            if self._active:
                self._release_access[key] = access
            return access

    async def _send_to_application(
        self,
        release_uid: str,
        method: str,
        url: httpx.URL,
        *,
        token: str,
        options: dict[str, Any],
        delegate: bool,
    ) -> httpx.Response:
        if delegate:
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
                message="Application call failed",
                target_system="resource_release",
                resource_release_uid=release_uid,
                requester_bound=delegate,
                duration_ms=round((time.monotonic() - started_at) * 1000, 3),
                error_type=failure,
                outcome="failed",
            )
            # Raised outside the handler: the error holds the request and its bearer token.
            raise BackendError(f"Call to release {release_uid} failed: {failure}")
        logger.info(
            "dependency.call.completed",
            message="Application call completed",
            target_system="resource_release",
            resource_release_uid=release_uid,
            requester_bound=delegate,
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


class PlatformClient:
    """Calls to the platform and its applications, for project code.

    ``delegation`` decides, when each call is made, whether it carries the delegation of the person
    the turn serves:

    - ``"auto"`` (the default): when the turn serves a person, and not otherwise;
    - ``"none"``: never, so the call is the Agent's own;
    - ``"required"``: always; when the turn serves nobody the call is refused before it is sent.

    The receiving operation decides what each call may do. A refused delegated call raises an error
    that is a ``PermissionError``; it is never retried without the delegation.
    """

    __slots__ = ("_binding", "_delegation")

    def __init__(self, binding: TurnRequesterBinding, delegation: Delegation) -> None:
        self._binding = binding
        self._delegation = delegation

    def __repr__(self) -> str:
        return f"PlatformClient(delegation={self._delegation!r})"

    def _delegates(self) -> bool:
        if self._delegation == "none":
            return False
        serving = self._binding.serving
        if self._delegation == "required" and not serving:
            raise RequesterBindingError(
                "This turn serves nobody, so the call cannot carry a delegation."
            )
        return serving

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
        """Call a platform API path, relative to the platform's base URL."""

        method = _method(method)
        path = _relative_path(path)
        options = _options(
            params=params,
            json=json,
            content=content,
            data=data,
            files=files,
            headers=headers,
        )
        return await self._binding.platform_request(
            method,
            path,
            options,
            delegate=self._delegates(),
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
        """Call another platform application.

        ``path`` is relative to the application's RPC URL. The access the platform grants is kept
        for the turn while it is valid.
        """

        release_uid = _canonical_uid(release_uid)
        method = _method(method)
        path = _relative_path(path)
        options = _options(
            params=params,
            json=json,
            content=content,
            data=data,
            files=files,
            headers=headers,
        )
        return await self._binding.release_request(
            release_uid,
            method,
            path,
            options,
            delegate=self._delegates(),
        )


_TURN_BINDING: ContextVar[TurnRequesterBinding | None] = ContextVar(
    "ms_tau_sdk_turn_requester_binding",
    default=None,
)
# The binding that carries the calls ``platform_client()`` makes outside a turn.
_PROCESS_BINDING: TurnRequesterBinding | None = None


def current_requester() -> Requester | None:
    """Return the person the current turn serves, or None.

    When a hosted turn or Task attempt starts, the platform names the person the Agent may act for,
    or nobody; that answer is the only source. Local mode, a runtime that does not verify who calls
    it, and code outside a turn have None.
    """

    binding = _TURN_BINDING.get()
    if binding is None or not binding.active:
        return None
    return binding.requester


def platform_client(*, delegation: Delegation = "auto") -> PlatformClient:
    """Return a client for calls to the platform and its applications.

    By default a call carries the delegation of the person the current turn serves, when it serves
    one, and none otherwise; ``delegation="none"`` and ``delegation="required"`` change that (see
    ``PlatformClient``). Outside a turn no call carries a delegation.
    """

    if delegation not in _DELEGATIONS:
        raise ValueError(f"delegation must be one of {', '.join(_DELEGATIONS)}")
    binding = _TURN_BINDING.get() or _PROCESS_BINDING
    if binding is None:
        raise TauSDKError("platform_client() needs the Agent runtime of this process")
    return PlatformClient(binding, delegation)


def bind_turn_requester(binding: TurnRequesterBinding) -> Token[TurnRequesterBinding | None]:
    """Make ``binding`` the current turn's binding."""

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


def register_runtime_platform(
    *,
    platform: _RequesterPlatform,
    application_http: Callable[[], httpx.AsyncClient],
) -> TurnRequesterBinding:
    """Make ``platform`` carry the calls ``platform_client()`` makes outside a turn."""

    global _PROCESS_BINDING
    binding = TurnRequesterBinding(
        session_uid=None,
        requester=None,
        lease_proof=None,
        platform=platform,
        application_http=application_http,
    )
    _PROCESS_BINDING = binding
    return binding


def release_runtime_platform(binding: TurnRequesterBinding) -> None:
    """Stop ``binding`` carrying calls made outside a turn."""

    global _PROCESS_BINDING
    binding.close()
    if _PROCESS_BINDING is binding:
        _PROCESS_BINDING = None


def canonical_requester_uid(value: object) -> str | None:
    """Return ``value`` when it is a canonical lowercase UUID, and None otherwise."""

    if not isinstance(value, str):
        return None
    try:
        return value if str(uuid.UUID(value)) == value else None
    except ValueError:
        return None


def _current_turn_binding() -> TurnRequesterBinding | None:
    """SDK-private: the binding of the turn the caller runs in."""

    return _TURN_BINDING.get()


async def _turn_application_access(release_uid: str) -> _TurnApplicationAccess:
    """SDK-private: the current turn's access to one application.

    The token acts for the person the turn serves, when it serves one, and is the Agent's own
    otherwise.
    """

    binding = _TURN_BINDING.get()
    if binding is None:
        raise RuntimeError("Application tools run only inside a turn")
    uid = _canonical_uid(release_uid)
    delegate = binding.serving
    access = await binding._release_access_for(uid, refresh=False, delegate=delegate)

    async def renew() -> str:
        return (await binding._release_access_for(uid, refresh=True, delegate=delegate)).token.value

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
        raise ValueError("A platform call takes a path such as /api/v1/..., never a URL")
    return path


def _options(*, headers: Mapping[str, str] | None, **values: Any) -> dict[str, Any]:
    if headers is not None:
        for name in headers:
            normalized = str(name).lower()
            if normalized in {"authorization", "proxy-authorization"} or normalized.startswith(
                "x-mainsequence-"
            ):
                raise ValueError(f"A platform call sets {name} itself")
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
    "Delegation",
    "PlatformClient",
    "Requester",
    "RequesterBindingError",
    "TurnRequesterBinding",
    "bind_turn_requester",
    "current_requester",
    "detach_turn_requester",
    "platform_client",
    "register_runtime_platform",
    "release_runtime_platform",
    "unbind_turn_requester",
]
