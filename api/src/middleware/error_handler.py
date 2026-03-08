import asyncio
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

logger = logging.getLogger(__name__)

# Timeout threshold for LLM calls (seconds).
# Multi-agent workflows (coordinator → RAG → domain agent) can take 40–60s.
LLM_TIMEOUT_SECONDS = 90


class LLMTimeoutError(Exception):
    """Raised when an LLM call exceeds the configured timeout."""


class RateLimitError(Exception):
    """Raised when the upstream API returns a rate-limit response."""

    def __init__(self, retry_after: int = 30):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after}s.")


def _error_response(
    status_code: int, error_type: str, message: str, detail: Any = None
) -> JSONResponse:
    """Build a structured JSON error response."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "type": error_type,
                "message": message,
                "detail": detail,
            }
        },
    )


def add_error_handlers(app: FastAPI) -> None:
    """Register exception handlers on the FastAPI app."""

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
        logger.warning(
            "Validation error on %s %s: %s",
            request.method,
            request.url.path,
            str(exc),
        )
        return _error_response(
            status_code=422,
            error_type="validation_error",
            message="Request validation failed",
            detail=exc.errors(),
        )

    @app.exception_handler(asyncio.TimeoutError)
    async def timeout_error_handler(request: Request, exc: asyncio.TimeoutError) -> JSONResponse:
        logger.error(
            "LLM timeout on %s %s (exceeded %ds)",
            request.method,
            request.url.path,
            LLM_TIMEOUT_SECONDS,
        )
        return _error_response(
            status_code=504,
            error_type="llm_timeout",
            message=(
                "The AI took too long to respond. Please try again with a simpler question "
                "or try again shortly."
            ),
            detail={"timeout_seconds": LLM_TIMEOUT_SECONDS},
        )

    @app.exception_handler(LLMTimeoutError)
    async def llm_timeout_handler(request: Request, exc: LLMTimeoutError) -> JSONResponse:
        logger.error(
            "LLM timeout on %s %s (exceeded %ds)",
            request.method,
            request.url.path,
            LLM_TIMEOUT_SECONDS,
        )
        return _error_response(
            status_code=504,
            error_type="llm_timeout",
            message=(
                "The AI took too long to respond. Please try again with a simpler question "
                "or try again shortly."
            ),
            detail={"timeout_seconds": LLM_TIMEOUT_SECONDS},
        )

    @app.exception_handler(RateLimitError)
    async def rate_limit_handler(request: Request, exc: RateLimitError) -> JSONResponse:
        logger.warning(
            "Rate limited on %s %s — retry after %ds",
            request.method,
            request.url.path,
            exc.retry_after,
        )
        response = _error_response(
            status_code=429,
            error_type="rate_limit",
            message=(
                "Too many requests. Please wait a moment before trying again. "
                f"Suggested retry after {exc.retry_after} seconds."
            ),
            detail={"retry_after_seconds": exc.retry_after},
        )
        response.headers["Retry-After"] = str(exc.retry_after)
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        logger.warning(
            "HTTP %d on %s %s: %s",
            exc.status_code,
            request.method,
            request.url.path,
            exc.detail,
        )
        return _error_response(
            status_code=exc.status_code,
            error_type="http_error",
            message=str(exc.detail),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled exception on %s %s",
            request.method,
            request.url.path,
        )
        return _error_response(
            status_code=500,
            error_type="internal_error",
            message="An unexpected error occurred",
            detail=str(exc) if app.debug else None,
        )

    # Expose handlers for testing (suppresses reportUnusedFunction)
    _ = (
        validation_error_handler,
        timeout_error_handler,
        llm_timeout_handler,
        rate_limit_handler,
        http_exception_handler,
        generic_exception_handler,
    )
