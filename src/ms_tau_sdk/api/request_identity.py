"""Request identity: who calls the runtime, and which sessions that caller may address.

``create_app()`` declares request identity for the platform launcher in
``app.state.mainsequence_request_identity``. In hosted mode
(``TauSDKSettings.request_identity_mode == "assertion"``) it also installs
``RequestIdentityMiddleware``, which admits a request only with the platform's signed assertion of
the kind its route requires: a platform assertion on ``/internal/*``, a caller assertion on every
other route. Handlers read the verified caller with ``current_caller()``, or from
``request.state.user`` and ``request.state.user_uid``.

A request that addresses an existing session must also come from the session's owner, from an
Organization admin, or, for a delegated child session, from the workload User of the Agent that
delegated to it; see ``require_session_access()``. Outside hosted mode nothing here changes how
requests are handled.

The raw caller assertion stays private to the request: ``request_caller_assertion()`` hands it
only to the turn that request starts, which presents it when it marks the turn active.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from contextvars import ContextVar

import structlog
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from ms_tau_sdk.backend.assertions import (
    ASSERTION_HEADER,
    AssertionKeysUnavailableError,
    AssertionKind,
    AssertionVerifier,
    CallerAssertion,
    InvalidAssertionError,
    VerifiedCaller,
)
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.backend.models import AgentSession, DirectoryUser
from ms_tau_sdk.errors import SessionNotFoundError, TauSDKError
from ms_tau_sdk.logging import bind_request_identity_log_fields
from ms_tau_sdk.runtime.provenance import CallerIdentityError
from ms_tau_sdk.settings import TauSDKSettings

logger = structlog.get_logger(__name__)

# The platform launcher reads this declaration from the application state before it serves the
# application. Its name and keys are the launcher's.
REQUEST_IDENTITY_STATE_ATTRIBUTE = "mainsequence_request_identity"
INTERNAL_ROUTE_PREFIX = "/internal"
_ASSERTION_HEADER_NAME = ASSERTION_HEADER.lower().encode("latin-1")
_REJECTION_DETAIL: dict[AssertionKind, str] = {
    "caller": "A valid caller assertion is required.",
    "platform": "A valid platform assertion is required.",
}
_UNAVAILABLE_DETAIL = "Caller authentication is unavailable."
SESSION_ACCESS_DENIED_DETAIL = (
    "Only the session's owner or an Organization admin can address this session."
)

_current_caller: ContextVar[VerifiedCaller | None] = ContextVar(
    "ms_tau_sdk_verified_caller",
    default=None,
)
# The verified raw caller assertion is kept in the request's own scope, not in request.state and
# not in a context variable, so that only the code that starts the request's turn reads it.
_CALLER_ASSERTION_SCOPE_KEY = "ms_tau_sdk.caller_assertion"
# The workload Users that one request looked up, by User UID: the workload, or None for a User
# that is not a workload or whose lookup failed. It lives only as long as its request.
_workload_lookups: ContextVar[dict[str, DirectoryUser | None] | None] = ContextVar(
    "ms_tau_sdk_workload_lookups",
    default=None,
)


class SessionAccessDeniedError(HTTPException):
    """A verified caller addressed a session that it neither owns nor administers."""

    def __init__(self) -> None:
        super().__init__(status_code=403, detail=SESSION_ACCESS_DENIED_DETAIL)


def current_caller() -> VerifiedCaller | None:
    """Return the caller that the current request's verified caller assertion names.

    It is None outside hosted mode and on the platform's own calls to ``/internal/*``.
    """

    return _current_caller.get()


def request_caller_assertion(request: Request) -> CallerAssertion | None:
    """Return the verified caller assertion of ``request`` for the turn or Task it starts.

    It is None outside hosted mode and on the platform's own calls. Only the chat and A2A routes
    read it, to present it when they mark their turn active or create or continue a Task; it is
    never logged, persisted, or passed to the model, a tool, a stream, or history. A caller this
    request admitted as the Agent that delegated to the session comes back marked as a workload,
    which is never a requester.
    """

    value = request.scope.get(_CALLER_ASSERTION_SCOPE_KEY)
    if not isinstance(value, CallerAssertion):
        return None
    lookups = _workload_lookups.get()
    if lookups is not None and lookups.get(value.caller.uid) is not None:
        # The request was admitted as the Agent that delegated to the session: its assertion names
        # a workload, never a requester.
        return dataclasses.replace(value, caller_is_workload=True)
    return value


def verified_user_uid(settings: TauSDKSettings) -> str | None:
    """Return the verified caller's User UID in hosted mode, and None otherwise.

    In hosted mode a request without a verified caller raises ``CallerIdentityError``: the
    caller is never taken from a header instead.
    """

    if settings.request_identity_mode != "assertion":
        return None
    caller = current_caller()
    if caller is None:
        raise CallerIdentityError((ASSERTION_HEADER,))
    return caller.uid


async def _workload_of(client: MainSequenceClient, user_uid: str) -> DirectoryUser | None:
    """Return the workload User ``user_uid`` names, as the platform's directory shows it, or None.

    A User that is not a workload, and a lookup that fails, give None, so the check fails closed.
    The answer is kept for the rest of the request only.
    """

    lookups = _workload_lookups.get()
    if lookups is not None and user_uid in lookups:
        return lookups[user_uid]
    workload: DirectoryUser | None = None
    try:
        found = await client.get_user(user_uid)
    except (TauSDKError, ValidationError) as error:
        logger.warning(
            "request_identity.caller_lookup.failed",
            message="The caller's User could not be looked up; the session is refused",
            error_type=type(error).__name__,
        )
    else:
        if found.uid == user_uid and found.identity_type == "workload" and found.agent_uid:
            workload = found
    if lookups is not None:
        lookups[user_uid] = workload
    return workload


async def _delegated_to_caller(
    client: MainSequenceClient,
    session: AgentSession,
    caller: VerifiedCaller,
) -> bool:
    """Whether ``session`` is a child session that the caller's own Agent delegated to.

    The session must name its parent session's Agent, the caller must be a workload User as the
    platform's directory shows it, and that workload must serve the same Agent. No request header
    is read.
    """

    parent_agent_uid = session.parent_session_agent_uid
    if not parent_agent_uid:
        return False
    workload = await _workload_of(client, caller.uid)
    return workload is not None and workload.agent_uid == parent_agent_uid


async def require_session_access(
    client: MainSequenceClient,
    session_uid: str,
) -> AgentSession | None:
    """Admit the current request to ``session_uid`` only for a caller allowed to address it.

    That is the session's owner, the User the platform recorded as its creator
    (``created_by_user_uid``); an Organization admin; or, for a delegated child session, the
    workload User of the Agent that delegated to it (``parent_session_agent_uid``). The check
    applies in hosted mode only and raises ``SessionAccessDeniedError`` (403). It returns the
    session when it had to read it.
    """

    if client.settings.request_identity_mode != "assertion":
        return None
    caller = current_caller()
    if caller is None or not session_uid:
        raise SessionAccessDeniedError()
    if caller.is_organization_admin:
        return None
    session = await client.get_session(session_uid)
    if session.created_by_user_uid == caller.uid:
        return session
    if await _delegated_to_caller(client, session, caller):
        return session
    raise SessionAccessDeniedError()


async def sessions_the_caller_may_address(
    client: MainSequenceClient,
    session_uids: Iterable[str],
) -> set[str] | None:
    """Return which of ``session_uids`` the current caller may address, by the rule of
    ``require_session_access()``.

    None means every session: outside hosted mode, or for an Organization admin. A session that
    no longer exists is left out.
    """

    if client.settings.request_identity_mode != "assertion":
        return None
    caller = current_caller()
    if caller is None:
        return set()
    if caller.is_organization_admin:
        return None
    allowed: set[str] = set()
    for session_uid in set(session_uids):
        if not session_uid:
            continue
        try:
            session = await client.get_session(session_uid)
        except SessionNotFoundError:
            continue
        if session.created_by_user_uid == caller.uid or await _delegated_to_caller(
            client, session, caller
        ):
            allowed.add(session_uid)
    return allowed


def _route_path(scope: Scope) -> str:
    """Return the path the router matches, the same way Starlette's routing derives it."""

    path = str(scope.get("path") or "")
    root_path = str(scope.get("root_path") or "")
    if root_path and path.startswith(root_path) and path != root_path:
        if path[len(root_path)] == "/":
            return path[len(root_path) :]
    return path


def required_assertion(scope: Scope) -> AssertionKind:
    """Name the assertion a route requires: a platform assertion on ``/internal/*`` only."""

    path = _route_path(scope)
    internal = path == INTERNAL_ROUTE_PREFIX or path.startswith(INTERNAL_ROUTE_PREFIX + "/")
    return "platform" if internal else "caller"


def _assertion(scope: Scope) -> str:
    values = [
        value for name, value in scope.get("headers", ()) if name.lower() == _ASSERTION_HEADER_NAME
    ]
    if len(values) > 1:
        raise InvalidAssertionError("The request carries more than one assertion")
    token = values[0].decode("latin-1").strip() if values else ""
    if not token:
        raise InvalidAssertionError("The request carries no assertion")
    return token


class RequestIdentityMiddleware:
    """Admit a hosted runtime's requests only with the platform's signed assertion."""

    def __init__(self, app: ASGIApp, *, verifier: AssertionVerifier) -> None:
        self.app = app
        self.verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            state.update(user=None, user_uid=None, auth_outcome="not_applicable")
            bind_request_identity_log_fields(scope, user_uid=None, auth_outcome="not_applicable")
            await self.app(scope, receive, send)
            return
        kind = required_assertion(scope)
        try:
            token = _assertion(scope)
            verified = await self.verifier.verify(token, kind=kind)
        except AssertionKeysUnavailableError as error:
            await self._deny(scope, receive, send, kind=kind, status_code=503, reason=str(error))
            return
        except InvalidAssertionError as error:
            await self._deny(scope, receive, send, kind=kind, status_code=401, reason=str(error))
            return
        caller = verified.caller
        user_uid = caller.uid if caller is not None else None
        state.update(
            user=caller,
            user_uid=user_uid,
            auth_outcome="authenticated",
            resource_release_uid=self.verifier.resource_release_uid,
            organization_environment_uid=self.verifier.organization_environment_uid,
        )
        bind_request_identity_log_fields(scope, user_uid=user_uid, auth_outcome="authenticated")
        if caller is not None:
            scope[_CALLER_ASSERTION_SCOPE_KEY] = CallerAssertion(
                caller=caller,
                expires_at=verified.expires_at,
                token=token,
            )
        caller_token = _current_caller.set(caller)
        lookups_token = _workload_lookups.set({})
        try:
            await self.app(scope, receive, send)
        finally:
            _workload_lookups.reset(lookups_token)
            _current_caller.reset(caller_token)
            scope.pop(_CALLER_ASSERTION_SCOPE_KEY, None)

    async def _deny(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        kind: AssertionKind,
        status_code: int,
        reason: str,
    ) -> None:
        outcome = "unavailable" if status_code == 503 else "rejected"
        scope["state"].update(user=None, user_uid=None, auth_outcome=outcome)
        bind_request_identity_log_fields(scope, user_uid=None, auth_outcome=outcome)
        logger.warning(
            "request_identity.rejected",
            message="Request identity rejected a request",
            required_assertion=kind,
            reason=reason,
            status_code=status_code,
        )
        if scope["type"] == "websocket":
            await WebSocketClose(code=1013 if status_code == 503 else 1008)(scope, receive, send)
            return
        response = JSONResponse(
            status_code=status_code,
            content={
                "detail": _UNAVAILABLE_DETAIL if status_code == 503 else _REJECTION_DETAIL[kind]
            },
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)


def install_request_identity(app: FastAPI, settings: TauSDKSettings) -> None:
    """Declare request identity for the platform launcher and, when hosted, enforce it.

    Call it before any other middleware is added, so that it is the innermost one.
    """

    mode = settings.request_identity_mode
    if mode == "assertion":
        app.add_middleware(
            RequestIdentityMiddleware,
            verifier=AssertionVerifier.from_settings(settings),
        )
    setattr(
        app.state,
        REQUEST_IDENTITY_STATE_ATTRIBUTE,
        {"installed": True, "mode": mode, "public_ingress": ()},
    )


__all__ = [
    "REQUEST_IDENTITY_STATE_ATTRIBUTE",
    "RequestIdentityMiddleware",
    "SessionAccessDeniedError",
    "current_caller",
    "install_request_identity",
    "request_caller_assertion",
    "require_session_access",
    "required_assertion",
    "sessions_the_caller_may_address",
    "verified_user_uid",
]
