"""Executable compatibility baseline for the ADR 56 migration.

This test intentionally imports the current Main Sequence TAU SDK application factory. During the
migration, only the import and construction boundary should change; any change to
the externally visible operation set must be an explicit contract decision.
"""

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
        ("POST", "/api/agents/{agent_uid}/responses"),
        ("POST", "/api/agents/{agent_uid}/responses/stream"),
        ("GET", "/api/chat"),
        ("POST", "/api/chat"),
        ("POST", "/api/chat/mock"),
        ("GET", "/api/chat/session-model"),
        ("POST", "/api/chat/session/cancel"),
        ("GET", "/health"),
        ("POST", "/internal/a2a/caller-deliveries:available"),
        ("POST", "/internal/a2a/dispatches:available"),
        ("GET", "/ready"),
        ("GET", "/version"),
    }
)


def test_adr56_freezes_current_http_operation_surface(sdk_app: FastAPI) -> None:
    schema = sdk_app.openapi()
    actual_operations = frozenset(
        (method.upper(), path)
        for path, path_item in schema["paths"].items()
        for method in path_item
        if method in HTTP_METHODS
    )

    assert actual_operations == EXPECTED_OPERATIONS
