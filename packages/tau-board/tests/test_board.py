from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from ms_tau_board.app import create_app
from ms_tau_board.config import BoardSettings, Profile, local_url
from ms_tau_board.logs import read_logs
from ms_tau_board.state import TABLES


def _store(root: Path, digest: str = "a1b2c3d4e5f6") -> Path:
    directory = root / digest
    directory.mkdir()
    with sqlite3.connect(directory / "runtime.sqlite3") as connection:
        connection.execute("CREATE TABLE schema_metadata(key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO schema_metadata VALUES('schema_version', '2')")
        for table in TABLES:
            if table == "sessions":
                connection.execute(
                    "CREATE TABLE sessions(uid TEXT, provider TEXT, model TEXT, thinking TEXT, "
                    "runtime_activity TEXT, created_at TEXT, updated_at TEXT)"
                )
                connection.execute(
                    "INSERT INTO sessions VALUES(?, 'openai', 'gpt-5.4', NULL, 'idle', ?, ?)",
                    (
                        "local-a1b2c3d4e5f6-default",
                        "2026-09-21T10:00:00+00:00",
                        "2026-09-21T10:00:01+00:00",
                    ),
                )
            elif table == "entries":
                connection.execute("CREATE TABLE entries(session_uid TEXT, entry_json TEXT)")
                connection.execute(
                    "INSERT INTO entries VALUES(?, ?)",
                    ("local-a1b2c3d4e5f6-default", '{"secret":"text"}'),
                )
            elif table == "a2a_tasks":
                connection.execute(
                    "CREATE TABLE a2a_tasks(uid TEXT, task_id TEXT, context_id TEXT, "
                    "agent_session_uid TEXT, status TEXT, status_timestamp TEXT, "
                    "cancellation_requested INTEGER, created_at TEXT, updated_at TEXT)"
                )
                connection.execute(
                    "INSERT INTO a2a_tasks VALUES('internal-1', 'task-1', ?, ?, "
                    "'completed', ?, 0, ?, ?)",
                    (
                        "local-a1b2c3d4e5f6-default",
                        "local-a1b2c3d4e5f6-default",
                        "2026-09-21T10:00:01+00:00",
                        "2026-09-21T10:00:00+00:00",
                        "2026-09-21T10:00:01+00:00",
                    ),
                )
            elif table == "a2a_task_events":
                connection.execute(
                    "CREATE TABLE a2a_task_events(task_uid TEXT, sequence INTEGER, uid TEXT, "
                    "event_type TEXT, status TEXT, message_uid TEXT, output_uid TEXT, "
                    "payload_json TEXT, created_at TEXT)"
                )
            elif table == "a2a_task_attempts":
                connection.execute(
                    "CREATE TABLE a2a_task_attempts(uid TEXT, task_uid TEXT, "
                    "attempt_number INTEGER, state TEXT, created_at TEXT, updated_at TEXT)"
                )
            elif table == "a2a_task_outputs":
                connection.execute(
                    "CREATE TABLE a2a_task_outputs(task_uid TEXT, artifact_id TEXT, "
                    "revision INTEGER, name TEXT, finalized INTEGER, "
                    "created_at TEXT, updated_at TEXT)"
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
        if request.url.path == "/api/chat/model-providers":
            return httpx.Response(
                200,
                json={
                    "schema_version": 1,
                    "providers": [{"provider": "openai", "models": [{"model": "gpt-5.4"}]}],
                },
            )
        if request.url.path == "/api/chat/session-model" and request.method == "PUT":
            return httpx.Response(
                200,
                json={
                    "sessionUid": "session-1",
                    "model": {"provider": "openai", "model": "gpt-5.4", "thinkingLevel": None},
                },
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
            catalog = await board.get(
                "/tau/api/chat/model-providers", headers={"Authorization": "Bearer secret"}
            )
            assert catalog.json()["providers"][0]["provider"] == "openai"
            assert "authorization" not in seen[-1].headers
            changed = await board.put(
                "/tau/api/chat/session-model",
                json={"sessionUid": "session-1", "provider": "openai", "model": "gpt-5.4"},
            )
            assert changed.json()["model"]["model"] == "gpt-5.4"
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


@pytest.mark.asyncio
async def test_task_tab_data_includes_task_and_related_session_events(tmp_path: Path) -> None:
    directory = _store(tmp_path)
    session_uid = "local-a1b2c3d4e5f6-default"
    with sqlite3.connect(directory / "runtime.sqlite3") as connection:
        for table in (
            "sessions",
            "a2a_tasks",
            "a2a_task_events",
            "a2a_task_attempts",
            "a2a_task_outputs",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute(
            "CREATE TABLE sessions(uid TEXT, provider TEXT, model TEXT, thinking TEXT, "
            "runtime_activity TEXT, created_at TEXT, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO sessions VALUES(?, 'openai', 'gpt-5.4', NULL, 'idle', ?, ?)",
            (session_uid, "2026-09-21T09:00:00+00:00", "2026-09-21T10:01:00+00:00"),
        )
        connection.execute(
            "CREATE TABLE a2a_tasks(uid TEXT, task_id TEXT, context_id TEXT, "
            "agent_session_uid TEXT, status TEXT, status_timestamp TEXT, "
            "cancellation_requested INTEGER, created_at TEXT, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO a2a_tasks VALUES('internal-1', 'task-1', ?, ?, 'completed', ?, 0, ?, ?)",
            (
                session_uid,
                session_uid,
                "2026-09-21T10:01:00+00:00",
                "2026-09-21T10:00:00+00:00",
                "2026-09-21T10:01:00+00:00",
            ),
        )
        connection.execute(
            "CREATE TABLE a2a_task_events(task_uid TEXT, sequence INTEGER, uid TEXT, "
            "event_type TEXT, status TEXT, message_uid TEXT, output_uid TEXT, "
            "payload_json TEXT, created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO a2a_task_events VALUES('internal-1', 1, 'event-1', "
            "'status', 'completed', NULL, NULL, '{}', '2026-09-21T10:01:00+00:00')"
        )
        connection.execute(
            "CREATE TABLE a2a_task_attempts(uid TEXT, task_uid TEXT, attempt_number INTEGER, "
            "state TEXT, created_at TEXT, updated_at TEXT)"
        )
        connection.execute(
            "CREATE TABLE a2a_task_outputs(task_uid TEXT, artifact_id TEXT, revision INTEGER, "
            "name TEXT, finalized INTEGER, created_at TEXT, updated_at TEXT)"
        )
    (directory / "logs" / "tau.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-09-21T10:00:15Z",
                "event": "a2a.task.started",
                "a2a_task_id": "task-1",
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": "2026-09-21T10:00:30Z",
                "event": "agent.model.failed",
                "session_uid": session_uid,
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": "2026-09-21T10:00:40Z",
                "event": "a2a.task.other",
                "a2a_task_id": "task-10",
                "session_uid": session_uid,
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": "2026-09-21T10:05:00Z",
                "event": "agent.later",
                "session_uid": session_uid,
            }
        )
        + "\n"
    )
    app = create_app(
        BoardSettings(
            profiles=(Profile("Local Tau", "http://127.0.0.1:8010"),),
            state_root=tmp_path,
            state_dir=directory,
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8788"
        ) as board:
            sessions = await board.get("/api/board/sessions")
            assert sessions.json()["sessions"][0]["uid"] == session_uid
            tasks = await board.get("/api/board/tasks", params={"sessionUid": session_uid})
            assert [task["task_id"] for task in tasks.json()["tasks"]] == ["task-1"]
            detail = await board.get("/api/board/tasks/task-1")
            assert detail.json()["events"][0]["status"] == "completed"
            timeline = await board.get("/api/board/tasks/task-1/logs")
            assert {record["event"] for record in timeline.json()["records"]} == {
                "a2a.task.started",
                "agent.model.failed",
            }
    first_page = read_logs(
        directory,
        limit=1,
        session=session_uid,
        task="task-1",
        related_session=True,
        since="2026-09-21T10:00:00+00:00",
        until="2026-09-21T10:01:05+00:00",
    )
    second_page = read_logs(
        directory,
        limit=1,
        offset=1,
        session=session_uid,
        task="task-1",
        related_session=True,
        since="2026-09-21T10:00:00+00:00",
        until="2026-09-21T10:01:05+00:00",
    )
    assert first_page["hasMore"] is True
    assert {first_page["records"][0]["event"], second_page["records"][0]["event"]} == {
        "a2a.task.started",
        "agent.model.failed",
    }


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


@pytest.mark.asyncio
async def test_settings_show_safe_environment_and_edit_only_local_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "MAINSEQUENCE_ENDPOINT=https://file-user:file-password@file.example/api?token=file-secret\n"
        "TAU_LOCAL_MODEL=gpt-old\n"
        "MAINSEQUENCE_ACCESS_TOKEN=secret-in-file\n"
    )
    monkeypatch.setenv(
        "MAINSEQUENCE_ENDPOINT", "https://user:password@running.example/api?token=secret"
    )
    monkeypatch.setenv("MAINSEQUENCE_ACCESS_TOKEN", "secret-in-process")
    settings = BoardSettings(
        profiles=(Profile("Local Tau", "http://127.0.0.1:8010"),),
        state_root=tmp_path,
        state_dir=None,
        env_file=env_path,
    )
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    app = create_app(settings, http_client=upstream)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8788"
        ) as board:
            settings_response = await board.get("/api/board/settings")
            assert settings_response.status_code == 200
            data = settings_response.json()
            backend = next(
                row for row in data["environment"] if row["name"] == "MAINSEQUENCE_ENDPOINT"
            )
            assert backend["value"] == "https://running.example [credentials or query hidden]"
            assert data["envFileValues"]["MAINSEQUENCE_ENDPOINT"]["value"] == (
                "https://file.example [credentials or query hidden]"
            )
            assert data["credentials"][0]["present"] is True
            assert data["envFileCredentials"]["MAINSEQUENCE_ACCESS_TOKEN"] is True
            assert "secret-in-file" not in json.dumps(data)
            assert "secret-in-process" not in json.dumps(data)
            assert "file-password" not in json.dumps(data)
            assert "file-secret" not in json.dumps(data)

            denied = await board.put(
                "/api/board/settings",
                json={"changes": {"MAINSEQUENCE_ENDPOINT": "https://new.example"}},
                headers={"Origin": "http://elsewhere.test"},
            )
            assert denied.status_code == 403
            secret_change = await board.put(
                "/api/board/settings",
                json={"changes": {"MAINSEQUENCE_ACCESS_TOKEN": "replacement"}},
            )
            assert secret_change.status_code == 400
            saved = await board.put(
                "/api/board/settings",
                json={
                    "changes": {
                        "MAINSEQUENCE_ENDPOINT": "https://new.example/api",
                        "TAU_LOCAL_MODEL": "gpt-new",
                    }
                },
            )
            assert saved.status_code == 200
            assert saved.json()["envFileValues"]["TAU_LOCAL_MODEL"]["value"] == "gpt-new"
            saved_backend = next(
                row for row in saved.json()["environment"] if row["name"] == "MAINSEQUENCE_ENDPOINT"
            )
            assert saved_backend["value"] == "https://running.example [credentials or query hidden]"
    await upstream.aclose()
    assert "MAINSEQUENCE_ACCESS_TOKEN=secret-in-file" in env_path.read_text()
    assert 'MAINSEQUENCE_ENDPOINT="https://new.example/api"' in env_path.read_text()
