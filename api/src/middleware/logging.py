"""Structured JSON logging with request-scoped context variables."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Context variables — set per-request and read by the JSON formatter
# ---------------------------------------------------------------------------

ctx_conversation_id: ContextVar[str | None] = ContextVar("ctx_conversation_id", default=None)
ctx_message_id: ContextVar[str | None] = ContextVar("ctx_message_id", default=None)
ctx_user_id: ContextVar[str | None] = ContextVar("ctx_user_id", default=None)
ctx_agent_name: ContextVar[str | None] = ContextVar("ctx_agent_name", default=None)
ctx_action: ContextVar[str | None] = ContextVar("ctx_action", default=None)
ctx_request_id: ContextVar[str | None] = ContextVar("ctx_request_id", default=None)


def set_logging_context(
    *,
    request_id: str | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
    user_id: str | None = None,
    agent_name: str | None = None,
    action: str | None = None,
) -> None:
    """Convenience helper to set multiple context variables at once."""
    if request_id is not None:
        ctx_request_id.set(request_id)
    if conversation_id is not None:
        ctx_conversation_id.set(conversation_id)
    if message_id is not None:
        ctx_message_id.set(message_id)
    if user_id is not None:
        ctx_user_id.set(user_id)
    if agent_name is not None:
        ctx_agent_name.set(agent_name)
    if action is not None:
        ctx_action.set(action)


def reset_logging_context() -> None:
    """Reset all context variables (typically at the end of a request)."""
    ctx_request_id.set(None)
    ctx_conversation_id.set(None)
    ctx_message_id.set(None)
    ctx_user_id.set(None)
    ctx_agent_name.set(None)
    ctx_action.set(None)


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line with contextual fields."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Inject request-scoped context when available
        context_fields: dict[str, str | None] = {
            "request_id": ctx_request_id.get(),
            "conversation_id": ctx_conversation_id.get(),
            "message_id": ctx_message_id.get(),
            "user_id": ctx_user_id.get(),
            "agent_name": ctx_agent_name.get(),
            "action": ctx_action.get(),
        }
        # Only include fields that are set
        context = {k: v for k, v in context_fields.items() if v is not None}
        if context:
            log_entry["context"] = context

        # Include exception info if present
        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_logging(log_level: str = "INFO") -> None:
    """Configure the root logger with structured JSON output.

    Parameters
    ----------
    log_level:
        One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.setLevel(numeric_level)
    # Remove any existing handlers to avoid duplicate output
    root.handlers.clear()
    root.addHandler(handler)

    # Quieten noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("azure").setLevel(logging.WARNING)
