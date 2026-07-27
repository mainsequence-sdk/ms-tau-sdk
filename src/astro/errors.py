"""Normalized application errors."""

from __future__ import annotations


class AstroError(RuntimeError):
    code = "astro_error"
    status_code = 500

    def __init__(self, message: str, *, detail: object | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class ConfigurationError(AstroError):
    code = "configuration_error"
    status_code = 500


class BackendError(AstroError):
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


class LeaseLostError(AstroError):
    code = "runtime_lease_lost"
    status_code = 409


class SessionBusyError(AstroError):
    code = "session_busy"
    status_code = 409


class SessionNotFoundError(AstroError):
    code = "session_not_found"
    status_code = 404
