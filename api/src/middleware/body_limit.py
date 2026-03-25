"""Middleware to reject oversized request bodies."""

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 65_536  # 64 KB — default for most endpoints
MAX_UPLOAD_BODY_BYTES = 20_971_520  # 20 MB — for chat endpoints with file attachments

# Paths that accept file attachments and need the higher limit.
_UPLOAD_PATHS = frozenset({"/api/v1/chat", "/api/v1/chat/stream"})


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        limit = MAX_UPLOAD_BODY_BYTES if request.url.path in _UPLOAD_PATHS else MAX_BODY_BYTES

        # Check Content-Length header first (fast reject)
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > limit:
            logger.warning(
                "Request body too large: %s bytes (max %d) for %s",
                content_length,
                limit,
                request.url.path,
            )
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "type": "payload_too_large",
                        "message": f"Request body exceeds {limit} byte limit",
                    }
                },
            )

        # Also enforce on actual body (Content-Length can be omitted or spoofed)
        if request.method in ("POST", "PUT", "PATCH"):
            body = await request.body()
            if len(body) > limit:
                logger.warning(
                    "Request body too large: %d bytes (max %d) for %s",
                    len(body),
                    limit,
                    request.url.path,
                )
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "type": "payload_too_large",
                            "message": f"Request body exceeds {limit} byte limit",
                        }
                    },
                )

        return await call_next(request)
