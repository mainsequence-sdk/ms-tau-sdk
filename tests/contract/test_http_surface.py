"""Executable contract for the Main Sequence TAU SDK HTTP operation surface."""

from fastapi import FastAPI

HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put"})

EXPECTED_OPERATIONS = frozenset(
    {
        ("POST", "/api/a2a/rpc"),
        ("GET", "/api/a2a/v1/extendedAgentCard"),
        ("POST", "/api/a2a/v1/message:send"),
        ("POST", "/api/a2a/v1/message:stream"),
        ("GET", "/api/a2a/v1/tasks"),
        ("GET", "/api/a2a/v1/tasks/{task_id}"),
        ("GET", "/api/a2a/v1/tasks/{task_id}/pushNotificationConfigs"),
        ("POST", "/api/a2a/v1/tasks/{task_id}/pushNotificationConfigs"),
        ("DELETE", "/api/a2a/v1/tasks/{task_id}/pushNotificationConfigs/{config_id}"),
        ("GET", "/api/a2a/v1/tasks/{task_id}/pushNotificationConfigs/{config_id}"),
        ("POST", "/api/a2a/v1/tasks/{task_id}:cancel"),
        ("GET", "/api/a2a/v1/tasks/{task_id}:subscribe"),
        ("GET", "/api/chat"),
        ("POST", "/api/chat"),
        ("POST", "/api/chat/mock"),
        ("GET", "/api/chat/session-model"),
        ("PUT", "/api/chat/session-model"),
        ("GET", "/api/chat/model-providers"),
        ("POST", "/api/chat/session/cancel"),
        ("GET", "/api/local/v1/sessions/{session_uid}/agent-inspection"),
        (
            "GET",
            "/api/local/v1/sessions/{session_uid}/extension-sources/{source_uid}",
        ),
        (
            "POST",
            "/api/local/v1/sessions/{session_uid}/tools/{tool_name}:validate",
        ),
        ("POST", "/api/local/v1/sessions/{session_uid}/tools/{tool_name}:test"),
        (
            "POST",
            "/api/local/v1/sessions/{session_uid}/tool-tests/{test_uid}:cancel",
        ),
        ("GET", "/health"),
        ("POST", "/internal/a2a/task-caller-delivery"),
        ("POST", "/internal/a2a/task-dispatch"),
        ("GET", "/ready"),
        ("GET", "/version"),
    }
)


def test_http_operation_surface_is_explicit(sdk_app: FastAPI) -> None:
    schema = sdk_app.openapi()
    actual_operations = frozenset(
        (method.upper(), path)
        for path, path_item in schema["paths"].items()
        for method in path_item
        if method in HTTP_METHODS
    )

    assert actual_operations == EXPECTED_OPERATIONS


def test_agent_targeted_one_shot_routes_are_absent(sdk_app: FastAPI) -> None:
    paths = sdk_app.openapi()["paths"]
    assert not any(path.startswith("/api/agents/") for path in paths)


def test_internal_a2a_control_signals_return_backend_compatible_success(
    sdk_app: FastAPI,
) -> None:
    schema = sdk_app.openapi()

    for path in (
        "/internal/a2a/task-caller-delivery",
        "/internal/a2a/task-dispatch",
    ):
        responses = schema["paths"][path]["post"]["responses"]
        assert "200" in responses
        assert "202" not in responses
