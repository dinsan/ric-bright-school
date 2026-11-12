from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Every machine-readable ``error.code`` the API can return.

    This is a public contract, documented in ``docs/errors.md`` (a test keeps the two in
    sync). Never rename or reuse a value: clients branch on it. Adding a value is fine.
    """

    VALIDATION_ERROR = "validation_error"
    DOMAIN_VALIDATION_FAILED = "domain_validation_failed"
    INVALID_TOKEN = "invalid_token"
    UNAUTHORIZED = "unauthorized"
    INVALID_CREDENTIALS = "invalid_credentials"
    FORBIDDEN = "forbidden"
    EMAIL_NOT_VERIFIED = "email_not_verified"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    HTTP_ERROR = "http_error"
    INTERNAL_ERROR = "internal_error"
    SERVICE_UNAVAILABLE = "service_unavailable"


class AppError(Exception):
    """Base class for errors that map cleanly onto an HTTP response."""

    status_code: int = 500
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.message = message or self.default_message
        self.details = details or []
        self.headers = headers or {}
        super().__init__(self.message)


class UnauthorizedError(AppError):
    status_code = 401
    code = ErrorCode.UNAUTHORIZED
    default_message = "Authentication is required."


class InvalidCredentialsError(AppError):
    """Wrong email/password, or a wrong current password when confirming a change."""

    status_code = 401
    code = ErrorCode.INVALID_CREDENTIALS
    default_message = "The email or password is incorrect."


class InvalidTokenError(AppError):
    """An emailed token or link code that is unknown, expired, or already used."""

    status_code = 400
    code = ErrorCode.INVALID_TOKEN
    default_message = "This link or code is invalid or has expired."


class EmailNotVerifiedError(AppError):
    status_code = 403
    code = ErrorCode.EMAIL_NOT_VERIFIED
    default_message = "Verify your email address before signing in."


class RateLimitedError(AppError):
    status_code = 429
    code = ErrorCode.RATE_LIMITED
    default_message = "Too many attempts. Try again later."

    def __init__(self, retry_after_seconds: int, message: str | None = None):
        super().__init__(message, headers={"Retry-After": str(max(1, retry_after_seconds))})
        self.retry_after_seconds = retry_after_seconds


class ForbiddenError(AppError):
    status_code = 403
    code = ErrorCode.FORBIDDEN
    default_message = "You are not allowed to perform this action."


class NotFoundError(AppError):
    status_code = 404
    code = ErrorCode.NOT_FOUND
    default_message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = 409
    code = ErrorCode.CONFLICT
    default_message = "The request conflicts with the current state of the resource."


class DomainValidationError(AppError):
    status_code = 422
    code = ErrorCode.DOMAIN_VALIDATION_FAILED
    default_message = "The request violates a business rule."


class ServiceUnavailableError(AppError):
    """A service the API depends on (other than the database) is unreachable."""

    status_code = 503
    code = ErrorCode.SERVICE_UNAVAILABLE
    default_message = "A service the API depends on is unavailable. Try again shortly."
