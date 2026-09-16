"""Normalized application errors."""

from __future__ import annotations


class TauSDKError(RuntimeError):
    code = "tau_sdk_error"
    status_code = 500

    def __init__(self, message: str, *, detail: object | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class ConfigurationError(TauSDKError):
    code = "configuration_error"
    status_code = 500


class BackendError(TauSDKError):
    code = "backend_error"
    status_code = 502

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: object | None = None,
    ) -> None:
        super().__init__(message, detail=detail)
        self.backend_status = status_code


class BackendConflictError(BackendError):
    code = "backend_conflict"
    status_code = 409


class LeaseLostError(TauSDKError):
    code = "runtime_lease_lost"
    status_code = 409


class SessionBusyError(TauSDKError):
    code = "session_busy"
    status_code = 409


class SessionNotFoundError(TauSDKError):
    code = "session_not_found"
    status_code = 404
