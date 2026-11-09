import logging
import time
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import request_id_ctx

access_logger = logging.getLogger("lumina.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id (honouring an inbound ``X-Request-ID``) and logs each request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid4().hex
        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            access_logger.info("%s %s (%.1f ms)", request.method, request.url.path, elapsed_ms)
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response
