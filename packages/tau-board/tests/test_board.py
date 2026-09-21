from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path

import httpx
import pytest
from ms_tau_board.app import create_app
from ms_tau_board.config import BoardSettings, Profile, local_url
from ms_tau_board.state import TABLES


def _store(root: Path, digest: str = "a1b2c3d4e5f6") -> Path:
    directory = root / digest
    directory.mkdir()
    with sqlite3.connect(directory / "runtime.sqlite3") as connection:
        connection.execute("CREATE TABLE schema_metadata(key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO schema_metadata VALUES('schema_version', '2')")
        for table in TABLES:
            if table == "sessions":
                connection.execute("CREATE TABLE sessions(uid TEXT)")
                connection.execute("INSERT INTO sessions VALUES('local-a1b2c3d4e5f6-default')")
            elif table == "entries":
                connection.execute("CREATE TABLE entries(session_uid TEXT, entry_json TEXT)")
                connection.execute(
                    "INSERT INTO entries VALUES(?, ?)",
                    ("local-a1b2c3d4e5f6-default", '{"secret":"text"}'),
                )
            elif table == "a2a_tasks":
                connection.execute("CREATE TABLE a2a_tasks(task_id TEXT, context_id TEXT)")
                connection.execute(
                    "INSERT INTO a2a_tasks VALUES('task-1', 'local-a1b2c3d4e5f6-default')"
                )
            else:
                connection.execute(f"CREATE TABLE {table}(id TEXT)")
    logs = directory / "logs"
    logs.mkdir()
    (logs / "tau.jsonl").write_text(
        '{"timestamp":"2026-09-21T10:00:00Z","level":"info","event":"agent.started","session_uid":"local-a1b2c3d4e5f6-default"}\n'
        '{"timestamp":"2026-09-21T10:00:01Z","level":"error","event":"agent.failed","a2a_task_id":"task-1"}\n'
    )
    return directory


@pytest.mark.asyncio
async def test_board_connects_proxies_and_inspects_state_without_writes(tmp_path: Path) -> None:
    digest = "a1b2c3d4e5f6"
    directory = _store(tmp_path, digest)
    before = (directory / "runtime.sqlite3").read_bytes()
    seen: list[httpx.Request] = []

    def tau(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/health":
            return httpx.Response(
                200, json={"runtime": "tau", "mode": "local", "workspace_digest": digest}
            )
        if request.url.path == "/ready":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream", "X-Agent-Session-Uid": "session-1"},
                content=b'data: {"type":"text-delta","textDelta":"Hello"}\n\ndata: [DONE]\n\n',
            )
        if request.url.path == "/api/a2a/v1/message:send":
            return httpx.Response(
                200,
                json={
                    "task": {
                        "id": "task-1",
                        "contextId": "session-1",
                        "status": {"state": "TASK_STATE_SUBMITTED"},
                    }
                },
            )
        if request.url.path == "/api/a2a/v1/message:stream":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b'data: {"task":{"id":"task-1"}}\n\n',
            )
        return httpx.Response(404)

    settings = BoardSettings(
        profiles=(Profile("Local Tau", "http://127.0.0.1:8010", "openai", "gpt-5.4"),),
        state_root=tmp_path,
        state_dir=None,
    )
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(tau))
    app = create_app(settings, http_client=upstream)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8788"
        ) as board:
            index = await board.get("/")
            assert index.status_code == 200
            assert "Tau Board" in index.text
            assert "'self'" in index.headers["Content-Security-Policy"]
            connected = await board.get("/api/board/connection")
            assert connected.json()["stateDir"] == str(directory)
            assert connected.json()["ready"] is True

            stream = await board.post(
                "/tau/api/chat",
                headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
                json={"message": "Hello"},
            )
            assert stream.status_code == 200
            assert stream.headers["X-Agent-Session-Uid"] == "session-1"
            assert "textDelta" in stream.text
            assert "authorization" not in seen[-1].headers
            assert seen[-1].url.port == 8010
            assert (await board.get("/tau/internal/a2a/task-dispatch")).status_code == 404
            task = await board.post(
                "/tau/api/a2a/v1/message:send",
                headers={
                    "A2A-Extensions": "https://mainsequence.ai/a2a/extensions/response-kind/v1",
                    "Authorization": "Bearer secret",
                },
                json={
                    "message": {
                        "messageId": "one",
                        "contextId": "session-1",
                        "parts": [{"text": "Do it"}],
                    },
                    "configuration": {"responseKind": "task"},
                },
            )
            assert task.json()["task"]["id"] == "task-1"
            assert seen[-1].headers["A2A-Extensions"].endswith("response-kind/v1")
            assert "authorization" not in seen[-1].headers
            task_stream = await board.post(
                "/tau/api/a2a/v1/message:stream",
                json={
                    "message": {
                        "messageId": "two",
                        "contextId": "session-1",
                        "parts": [{"text": "Follow"}],
                    }
                },
            )
            assert '"task-1"' in task_stream.text

            summary = await board.get("/api/board/state")
            assert summary.json()["tables"]["sessions"] == 1
            rows = await board.get("/api/board/state/entries")
            assert rows.json()["rows"][0]["entry_json"] == "[open row to view]"
            rowid = rows.json()["rows"][0]["board_rowid"]
            detail = await board.get(f"/api/board/state/entries/{rowid}")
            assert "secret" in detail.json()["row"]["entry_json"]
            errors = await board.get("/api/board/logs?level=error&task=task-1")
            assert [item["event"] for item in errors.json()["records"]] == ["agent.failed"]

    await upstream.aclose()
    assert (directory / "runtime.sqlite3").read_bytes() == before


@pytest.mark.asyncio
async def test_profile_override_and_nonlocal_endpoint_are_rejected(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def tau(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"runtime": "tau", "mode": "managed"})

    settings = BoardSettings(
        profiles=(Profile("Local Tau", "http://127.0.0.1:8787"),),
        state_root=tmp_path,
        state_dir=tmp_path,
    )
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(tau))
    app = create_app(settings, http_client=upstream)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8788"
        ) as board:
            await board.get("/api/board/config")
            changed = await board.put(
                "/api/board/config",
                json={
                    "profiles": [
                        {
                            "name": "Other",
                            "url": "http://127.0.0.1:8010",
                            "provider": "openai",
                            "model": "gpt-5.4",
                        }
                    ],
                    "selectedProfile": "Other",
                },
            )
            assert changed.json()["tauUrl"] == "http://127.0.0.1:8010"
            denied = await board.get("/api/board/connection")
            assert denied.status_code == 503
            assert "local-mode" in denied.json()["error"]
            cross_origin = await board.put(
                "/api/board/config", headers={"Origin": "http://evil.test"}, json={}
            )
            assert cross_origin.status_code == 403
    await upstream.aclose()
    assert calls[0].url.port == 8010


def test_loopback_validation_and_asset_budget() -> None:
    assert local_url("http://127.0.0.1:8010/") == "http://127.0.0.1:8010"
    with pytest.raises(ValueError):
        local_url("http://example.com:8010")
    with pytest.raises(ValueError):
        local_url("http://127.0.0.1:8010/private")
    assets = Path(__file__).parents[1] / "src/ms_tau_board/static"
    files = [path for path in assets.iterdir() if path.is_file()]
    assert sum(path.stat().st_size for path in files) < 800 * 1024
    assert sum(len(gzip.compress(path.read_bytes())) for path in files) < 120 * 1024
    assert (assets / "app.js").stat().st_size < 50 * 1024
    html = (assets / "index.html").read_text()
    assert '<script src="http' not in html
    assert '<link href="http' not in html
    assert "bulma.min.css" in html
