"""Middleware to reject oversized request bodies."""

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 65_536  # 64 KB


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_BYTES:
            logger.warning(
                "Request body too large: %s bytes (max %d)",
                content_length,
                MAX_BODY_BYTES,
            )
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "type": "payload_too_large",
                        "message": f"Request body exceeds {MAX_BODY_BYTES} byte limit",
                    }
                },
            )
        return await call_next(request)
