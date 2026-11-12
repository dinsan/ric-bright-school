"""One consistent error envelope for every failure:

{"error": {"code": "...", "message": "...", "details": [...], "request_id": "..."}}

``code`` is always an ``ErrorCode`` member. The full list is in ``docs/errors.md``.
"""

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError, ErrorCode

logger = logging.getLogger("lumina.errors")

# Errors raised by the framework itself (routing), mapped onto stable codes. Any other status
# falls back to ErrorCode.HTTP_ERROR rather than inventing an undocumented code.
_HTTP_STATUS_CODES: dict[int, ErrorCode] = {
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
}


def _response(
    request: Request,
    status_code: int,
    code: ErrorCode,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    response_headers = dict(headers or {})
    if request_id:
        # Set here too: unhandled exceptions (500) are rendered outside the request-context
        # middleware, which would otherwise never add the header.
        response_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status_code,
        headers=response_headers,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
                "request_id": request_id,
            }
        },
    )


async def handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return _response(
        request, exc.status_code, exc.code, exc.message, exc.details, headers=exc.headers
    )


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = [
        {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
        for err in exc.errors()
    ]
    return _response(request, 422, ErrorCode.VALIDATION_ERROR, "The request is invalid.", details)


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_STATUS_CODES.get(exc.status_code, ErrorCode.HTTP_ERROR)
    # Keep headers such as `Allow` (required on 405) or `WWW-Authenticate`.
    return _response(request, exc.status_code, code, str(exc.detail), headers=exc.headers)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception", exc_info=exc)
    return _response(request, 500, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)
