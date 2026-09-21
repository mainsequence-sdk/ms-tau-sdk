"""Standalone loopback application for Tau Board."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import env_file, logs, state
from .config import (
    BoardSettings,
    Profile,
    public_environment_value,
    safe_environment_snapshot,
)
from .proxy import TauUnavailable, forward, probe

STATIC = Path(__file__).parent / "static"
COOKIE = "tau_board_session"
SESSION_SECONDS = 24 * 60 * 60


class SecurityHeadersMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        async def wrapped_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                    "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                    "frame-ancestors 'none'"
                )
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        await self.app(scope, receive, wrapped_send)


@dataclass(slots=True)
class BrowserSession:
    profiles: dict[str, Profile]
    selected: str
    health: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_used: float = field(default_factory=time.monotonic)


def _session(request: Request) -> tuple[str, BrowserSession, bool]:
    sessions: dict[str, BrowserSession] = request.app.state.sessions
    now = time.monotonic()
    for key in tuple(sessions):
        if now - sessions[key].last_used > SESSION_SECONDS:
            del sessions[key]
    token = request.cookies.get(COOKIE, "")
    if token in sessions:
        session = sessions[token]
        session.last_used = now
        return token, session, False
    if len(sessions) >= 64:
        oldest = min(sessions, key=lambda item: sessions[item].last_used)
        del sessions[oldest]
    token = uuid.uuid4().hex
    profiles = {profile.name: profile for profile in request.app.state.settings.profiles}
    session = BrowserSession(profiles=profiles, selected=next(iter(profiles)))
    sessions[token] = session
    return token, session, True


def _cookie(response: Response, token: str, new: bool) -> Response:
    if new:
        response.set_cookie(
            COOKIE, token, httponly=True, samesite="strict", secure=False, max_age=SESSION_SECONDS
        )
    return response


def _selected(request: Request, session: BrowserSession) -> Profile:
    name = request.headers.get("x-tau-board-profile") or request.query_params.get("profile")
    if name is None:
        name = session.selected
    try:
        return session.profiles[name]
    except KeyError as error:
        raise ValueError("Unknown Tau endpoint profile") from error


def _directory(request: Request, session: BrowserSession, profile: Profile) -> Path:
    settings: BoardSettings = request.app.state.settings
    if profile.state_dir:
        directory = profile.state_dir
    elif settings.state_dir:
        directory = settings.state_dir
    else:
        health = session.health.get(profile.name)
        digest = health.get("workspace_digest") if health else None
        if not isinstance(digest, str) or not digest:
            raise ValueError("Connect to Tau before deriving its local state directory")
        directory = settings.state_root / digest
    return directory


def _state_warning(session: BrowserSession, profile: Profile, directory: Path) -> str | None:
    health = session.health.get(profile.name)
    digest = health.get("workspace_digest") if health else None
    if directory.exists() and digest and directory.name != digest:
        return "Selected state directory differs from Tau's workspace digest"
    return None


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return origin == f"http://{request.headers.get('host', '')}"


async def index(request: Request) -> Response:
    return FileResponse(STATIC / "index.html")


async def config(request: Request) -> Response:
    token, session, new = _session(request)
    if request.method == "PUT":
        if not _origin_allowed(request):
            response = JSONResponse({"error": "Invalid browser origin"}, status_code=403)
            return _cookie(response, token, new)
        if len(await request.body()) > 20_000:
            response = JSONResponse({"error": "Configuration is too large"}, status_code=413)
            return _cookie(response, token, new)
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError("Configuration must be an object")
        if "profiles" in value:
            raw_profiles = value["profiles"]
            if not isinstance(raw_profiles, list) or not 1 <= len(raw_profiles) <= 16:
                raise ValueError("Profiles must be a list of 1-16 entries")
            profiles = [Profile.from_dict(item) for item in raw_profiles if isinstance(item, dict)]
            names_are_unique = len({item.name for item in profiles}) == len(profiles)
            if len(profiles) != len(raw_profiles) or not names_are_unique:
                raise ValueError("Profiles must be distinct objects")
            session.profiles = {item.name: item for item in profiles}
            session.health.clear()
        selected = value.get("selectedProfile", session.selected)
        if not isinstance(selected, str) or selected not in session.profiles:
            raise ValueError("Unknown selected profile")
        session.selected = selected
    profile = session.profiles[session.selected]
    try:
        directory = str(_directory(request, session, profile))
    except ValueError:
        directory = str(profile.state_dir or request.app.state.settings.state_dir or "")
    return _cookie(
        JSONResponse(
            {
                "profiles": [item.public() for item in session.profiles.values()],
                "selectedProfile": session.selected,
                "tauUrl": profile.url,
                "stateDir": directory,
                "port": request.app.state.settings.port,
            }
        ),
        token,
        new,
    )


async def connection(request: Request) -> Response:
    token, session, new = _session(request)
    profile = _selected(request, session)
    health = await probe(request.app.state.http, profile)
    session.health[profile.name] = health
    try:
        directory = str(_directory(request, session, profile))
        state_error = _state_warning(session, profile, Path(directory))
    except ValueError as error:
        directory = str(profile.state_dir or request.app.state.settings.state_dir or "")
        state_error = str(error)
    try:
        ready = await request.app.state.http.get(profile.url + "/ready", timeout=5)
        readiness = ready.status_code == 200
    except httpx.HTTPError:
        readiness = False
    return _cookie(
        JSONResponse(
            {
                "profile": profile.public(),
                "health": health,
                "ready": readiness,
                "stateDir": directory,
                "stateError": state_error,
            }
        ),
        token,
        new,
    )


async def settings_view(request: Request) -> Response:
    token, session, new = _session(request)
    env_path: Path = request.app.state.settings.env_file or Path.cwd() / ".env"
    if request.method == "PUT":
        if not _origin_allowed(request):
            return _cookie(
                JSONResponse({"error": "Invalid browser origin"}, status_code=403), token, new
            )
        if len(await request.body()) > 12_000:
            return _cookie(
                JSONResponse({"error": "Settings request is too large"}, status_code=413),
                token,
                new,
            )
        payload = await request.json()
        changes = payload.get("changes") if isinstance(payload, dict) else None
        if not isinstance(changes, dict) or any(
            not isinstance(name, str) or not isinstance(value, str)
            for name, value in changes.items()
        ):
            raise ValueError("Settings changes must be a map of names to strings")
        await asyncio.to_thread(env_file.write_values, env_path, changes)
    file_values = await asyncio.to_thread(env_file.read_values, env_path)
    file_credentials = await asyncio.to_thread(env_file.credential_presence, env_path)
    file_exists = await asyncio.to_thread(env_path.is_file)
    exposed_file_values = {}
    for name, raw in file_values.items():
        value, redacted = public_environment_value(name, raw)
        exposed_file_values[name] = {"value": value, "redacted": redacted}
    profile = session.profiles[session.selected]
    try:
        directory = str(_directory(request, session, profile))
    except ValueError:
        directory = None
    return _cookie(
        JSONResponse(
            {
                **request.app.state.environment_snapshot,
                "envFile": str(env_path),
                "envFileExists": file_exists,
                "envFileValues": exposed_file_values,
                "envFileCredentials": file_credentials,
                "selectedProfile": profile.public(),
                "effectiveStateDir": directory,
                "boardPort": request.app.state.settings.port,
            }
        ),
        token,
        new,
    )


async def state_summary(request: Request) -> Response:
    token, session, new = _session(request)
    profile = _selected(request, session)
    directory = _directory(request, session, profile)
    result = await asyncio.to_thread(state.summary, directory)
    result["warning"] = _state_warning(session, profile, directory)
    return _cookie(JSONResponse(result), token, new)


async def state_rows(request: Request) -> Response:
    token, session, new = _session(request)
    directory = _directory(request, session, _selected(request, session))
    try:
        limit = int(request.query_params.get("limit", "50"))
        offset = int(request.query_params.get("offset", "0"))
    except ValueError as error:
        raise ValueError("Pagination must use integers") from error
    result = await asyncio.to_thread(
        state.list_rows, directory, request.path_params["table"], limit=limit, offset=offset
    )
    return _cookie(JSONResponse(result), token, new)


async def state_row(request: Request) -> Response:
    token, session, new = _session(request)
    directory = _directory(request, session, _selected(request, session))
    result = await asyncio.to_thread(
        state.row_detail, directory, request.path_params["table"], request.path_params["rowid"]
    )
    return _cookie(JSONResponse(result), token, new)


async def log_records(request: Request) -> Response:
    token, session, new = _session(request)
    profile = _selected(request, session)
    directory = _directory(request, session, profile)
    try:
        limit = int(request.query_params.get("limit", "100"))
    except ValueError as error:
        raise ValueError("Log limit must be an integer") from error
    result = await asyncio.to_thread(
        logs.read_logs,
        directory,
        limit=limit,
        level=request.query_params.get("level", ""),
        session=request.query_params.get("session", ""),
        task=request.query_params.get("task", ""),
        event=request.query_params.get("event", ""),
        since=request.query_params.get("since", ""),
    )
    result["warning"] = _state_warning(session, profile, directory)
    return _cookie(JSONResponse(result), token, new)


async def tau_proxy(request: Request) -> Response:
    token, session, new = _session(request)
    if request.method != "GET" and not _origin_allowed(request):
        response = JSONResponse({"error": "Invalid browser origin"}, status_code=403)
        return _cookie(response, token, new)
    profile = _selected(request, session)
    result = await forward(request, client=request.app.state.http, profile=profile)
    return _cookie(result, token, new)


async def _error(request: Request, error: Exception) -> Response:
    status = 503 if isinstance(error, TauUnavailable) else 400
    return JSONResponse({"error": str(error)}, status_code=status)


def create_app(
    settings: BoardSettings | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> Starlette:
    """Create the board process's application; never constructs the Tau runtime."""
    resolved = settings or BoardSettings.from_environment()

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        if http_client is not None:
            app.state.http = http_client
            yield
            return
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5, read=None, write=60, pool=5),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            app.state.http = client
            yield

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/", index),
            Route("/api/board/config", config, methods=["GET", "PUT"]),
            Route("/api/board/connection", connection),
            Route("/api/board/settings", settings_view, methods=["GET", "PUT"]),
            Route("/api/board/state", state_summary),
            Route("/api/board/state/{table}/{rowid:int}", state_row),
            Route("/api/board/state/{table}", state_rows),
            Route("/api/board/logs", log_records),
            Route("/tau/{path:path}", tau_proxy, methods=["GET", "POST"]),
            Mount("/assets", StaticFiles(directory=STATIC)),
        ],
        exception_handlers={ValueError: _error, state.StateError: _error, TauUnavailable: _error},
    )
    app.state.settings = resolved
    app.state.environment_snapshot = safe_environment_snapshot()
    app.state.sessions = {}

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    return app
