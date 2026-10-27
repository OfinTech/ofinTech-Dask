"""Exceptions that map to HTTP responses with a stable error code and a readable message."""

from __future__ import annotations

from typing import Any


class ConfigError(RuntimeError):
    """The engine cannot start with the given settings."""


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None,
                 details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status:
            self.status = status
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return body


class Unauthorized(AppError):
    status = 401
    code = "unauthorized"


class Forbidden(AppError):
    status = 403
    code = "forbidden"


class NotFound(AppError):
    status = 404
    code = "not_found"


class Conflict(AppError):
    status = 409
    code = "conflict"


class Invalid(AppError):
    status = 422
    code = "invalid"


class UpstreamError(AppError):
    """A connected service (LLM provider, accounting system, Google, ...) failed."""

    status = 502
    code = "upstream_error"


class NotConfigured(AppError):
    status = 409
    code = "not_configured"
