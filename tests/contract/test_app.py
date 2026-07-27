from fastapi.testclient import TestClient

from astro.app import create_app
from astro.settings import Settings


def test_health_reports_tau_runtime(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            backend_url="http://backend:8000",
            runtime_credential_id="credential-id",
            runtime_credential_secret="credential-secret",
            project_root=tmp_path,
        )
    )

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["runtime"] == "tau"
    assert response.json()["loaded_sessions"] == 0


def test_readiness_and_version_report_tau_runtime(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            backend_url="http://backend:8000",
            runtime_credential_id="credential-id",
            runtime_credential_secret="credential-secret",
            project_root=tmp_path,
        )
    )

    with TestClient(app) as client:
        readiness = client.get("/ready")
        version = client.get("/version")

    assert readiness.status_code == 200
    assert readiness.json()["runtime"] == "tau"
    assert version.status_code == 200
    assert version.json()["version"] == "4.0.0"


def test_mock_chat_uses_assistant_ui_sse(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            backend_url="http://backend:8000",
            runtime_credential_id="credential-id",
            runtime_credential_secret="credential-secret",
            project_root=tmp_path,
        )
    )

    with TestClient(app) as client:
        response = client.post("/api/chat/mock", json={"message": "hello"})

    assert response.status_code == 200
    assert '"type":"text-delta"' in response.text
    assert '"textDelta":"hello"' in response.text
    assert "data: [DONE]" in response.text
