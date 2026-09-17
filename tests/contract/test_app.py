from importlib.metadata import version

from httpx import AsyncClient

import ms_tau_sdk
from ms_tau_sdk import TauSDKSettings, __version__, create_app


def test_public_sdk_construction_surface():
    assert callable(create_app)
    assert TauSDKSettings.__name__ == "TauSDKSettings"
    assert set(ms_tau_sdk.__all__) == {
        "RUNTIME_HEALTH_ABI_VERSION",
        "TauSDKSettings",
        "__version__",
        "create_app",
        "register_deployment_readiness_hook",
    }
    assert __version__ == version("ms-tau-sdk")


async def test_health_reports_tau_runtime(sdk_client: AsyncClient):
    response = await sdk_client.get("/health")

    assert response.status_code == 200
    assert response.json()["runtime"] == "tau"
    assert response.json()["loaded_sessions"] == 0


async def test_readiness_and_version_report_tau_runtime(sdk_client: AsyncClient):
    readiness = await sdk_client.get("/ready")
    response = await sdk_client.get("/version")

    assert readiness.status_code == 200
    assert readiness.json()["runtime"] == "tau"
    assert response.status_code == 200
    assert response.json()["version"] == version("ms-tau-sdk")


async def test_mock_chat_uses_assistant_ui_sse(sdk_client: AsyncClient):
    response = await sdk_client.post("/api/chat/mock", json={"message": "hello"})

    assert response.status_code == 200
    assert '"type":"text-delta"' in response.text
    assert '"textDelta":"hello"' in response.text
    assert "data: [DONE]" in response.text


async def test_local_mode_rejects_only_registered_agent_orchestration_routes(
    asgi_client,
    tmp_path,
):
    settings = TauSDKSettings(
        _env_file=None,
        workspace=tmp_path,
        auth_mode="jwt",
        local_mode=True,
        access_token="access-token",
        refresh_token="refresh-token",
        local_provider="openai",
        local_model="gpt-5.4",
    )
    app = create_app(settings)

    async with asgi_client(app) as http:
        response = await http.post(
            "/api/agents/agent-1/responses",
            json={},
        )
        dispatch = await http.post(
            "/internal/a2a/dispatches:available",
            json={},
        )

    for rejected in (response, dispatch):
        assert rejected.status_code == 409
        assert rejected.json()["error"] == "local_mode_capability_unsupported"
        assert rejected.json()["detail"]["mode"] == "local"
