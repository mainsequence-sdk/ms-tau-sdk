from importlib.metadata import version

from httpx import AsyncClient

from ms_tau_sdk import TauSDKSettings, create_app


def test_public_sdk_construction_surface():
    assert callable(create_app)
    assert TauSDKSettings.__name__ == "TauSDKSettings"


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
